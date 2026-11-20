#!/bin/bash
# GUI-обёртка над скриптом очистки системы (zenity)
# Запуск: bash cleaner_gui.sh   (или chmod +x && ./cleaner_gui.sh)

set -uo pipefail

# ---------- Проверки ----------
command -v zenity >/dev/null 2>&1 || {
    echo "zenity не установлен. Установите: sudo apt install zenity" >&2
    exit 1
}

# ---------- Выбор шагов ----------
CHOICE=$(zenity --list --checklist \
    --title="🧹 Очистка системы Linux" \
    --text="Выберите, что очищать:" \
    --column="✓" --column="Шаг" --column="Описание" \
    TRUE  "snap"     "Удалить старые ревизии snap-пакетов" \
    TRUE  "flatpak"  "Удалить неиспользуемые flatpak и их данные" \
    TRUE  "apt"      "apt autoremove + apt clean" \
    TRUE  "journal"  "Журналы systemd старше 7 дней" \
    TRUE  "cache"    "~/.cache/* и /var/tmp/*" \
    --width=620 --height=380 \
    --separator="," 2>/dev/null) || exit 0

[ -z "$CHOICE" ] && { zenity --info --text="Ничего не выбрано."; exit 0; }

# Превращаем "snap,apt" в удобный поиск
SELECTED=",$CHOICE,"
need() { [[ "$SELECTED" == *",$1,"* ]]; }

# ---------- Подтверждение ----------
zenity --question --title="Подтверждение" --width=420 \
    --text="Будут выполнены шаги:\n\n$(echo "$CHOICE" | tr ',' '\n' | sed 's/^/ • /')\n\nПродолжить?" \
    || exit 0

# ---------- Временные файлы ----------
LOG_FILE=$(mktemp /tmp/cleaner.XXXXXX.log)        # лог worker'а (stderr)
RESULT_FILE=$(mktemp /tmp/cleaner.XXXXXX.result)  # финальное сообщение
FIFO=$(mktemp -u /tmp/cleaner.XXXXXX.fifo)
mkfifo "$FIFO"

cleanup() {
    exec 3>&- 2>/dev/null || true
    rm -f "$FIFO" "$LOG_FILE" "$RESULT_FILE"
}
trap cleanup EXIT

# ---------- Функция прогресса ----------
progress() { echo "$1"; echo "# $2"; }

# ---------- Основной воркер ----------
worker() {
    local before after freed_kb freed_mb errors=0

    before=$(df / | awk 'NR==2 {print $4}')
    progress 2 "Свободно ДО: $(( before / 1024 )) MB"

    # ===== Snap =====
    if need snap; then
        progress 5 "Snap: ищем старые ревизии…"
        local snap_list
        snap_list=$(LANG=en_US.UTF-8 snap list --all 2>/dev/null | awk '/disabled/{print $1, $3}')
        if [ -z "$snap_list" ]; then
            progress 10 "Snap: старых ревизий нет"
        else
            local total n=0
            total=$(echo "$snap_list" | wc -l)
            while read -r snapname revision; do
                [ -z "${snapname:-}" ] && continue
                n=$((n+1))
                local pct=$(( 5 + (n * 15) / total ))
                progress "$pct" "Snap: удаляем $snapname (rev $revision) [$n/$total]"
                sudo snap remove "$snapname" --revision="$revision" >/dev/null 2>&1 \
                    || { echo "snap $snapname (rev $revision): ошибка" >&2; errors=$((errors+1)); }
            done <<< "$snap_list"
        fi
    fi

    # ===== Flatpak =====
    if need flatpak; then
        progress 22 "Flatpak: удаляем неиспользуемое…"
        sudo flatpak uninstall --unused --delete-data -y >/dev/null 2>&1 || true
        progress 27 "Flatpak: repair…"
        flatpak repair >/dev/null 2>&1 || true
    fi

    # ===== Apt =====
    if need apt; then
        progress 32 "Apt: autoremove…"
        sudo apt autoremove -y >/dev/null 2>&1 || { echo "apt autoremove: ошибка" >&2; errors=$((errors+1)); }
        progress 40 "Apt: clean…"
        sudo apt clean >/dev/null 2>&1 || true
    fi

    # ===== Journal =====
    if need journal; then
        progress 45 "Journal: обрезаем до 7 дней…"
        sudo journalctl --vacuum-time=7d >/dev/null 2>&1 || true
    fi

    # ===== Кеши и временные файлы =====
    if need cache; then
        progress 55 "Кеш: очищаем ~/.cache…"
        rm -rf "${HOME:?}/.cache/"* 2>/dev/null || true
        progress 65 "Кеш: очищаем /var/tmp…"
        sudo rm -rf /var/tmp/* 2>/dev/null || true
    fi

    # ===== Итог =====
    progress 90 "Считаем освобождённое место…"
    after=$(df / | awk 'NR==2 {print $4}')
    freed_kb=$(( after - before ))
    freed_mb=$(( freed_kb / 1024 ))

    local human
    if [ "$freed_mb" -ge 1024 ]; then
        human=$(awk -v mb="$freed_mb" 'BEGIN{printf "%.2f GB", mb/1024}')
    else
        human="${freed_mb} MB"
    fi

    # финальное сообщение — в отдельный файл, а не в stderr
    if [ "$freed_kb" -ge 0 ]; then
        echo "Освобождено: ${human}" > "$RESULT_FILE"
    else
        human=${human#-}
        echo "Внимание: свободного места стало меньше на ${human}" > "$RESULT_FILE"
    fi

    progress 100 "Готово"

    [ "$errors" -gt 0 ] && return 2
    return 0
}

# ---------- Запуск прогресс-бара ----------
zenity --progress \
    --title="Очистка системы" \
    --text="Подготовка…" \
    --percentage=0 \
    --auto-close \
    --no-cancel \
    --width=480 < "$FIFO" &
ZENITY_PID=$!

# Открываем FIFO на запись (fd 3). Порядок важен: сначала читатель (zenity), потом писатель.
exec 3>"$FIFO"

# ---------- Запуск воркера ----------
worker 1>&3 2>"$LOG_FILE" &
WORKER_PID=$!

# Ждём именно воркер и получаем ЕГО код выхода
wait "$WORKER_PID"
EXIT_CODE=$?

# Закрываем FIFO → zenity получит EOF
exec 3>&-

# Даём zenity шанс корректно закрыться, затем страхуемся kill
for _ in 1 2 3 4 5 6 7 8 9 10; do
    kill -0 "$ZENITY_PID" 2>/dev/null || break
    sleep 0.2
done
kill "$ZENITY_PID" 2>/dev/null || true
wait "$ZENITY_PID" 2>/dev/null || true

# ---------- Финальное окно ----------
LOG=$(cat "$LOG_FILE" 2>/dev/null || true)

case "$EXIT_CODE" in
    0)
        FINAL=$(cat "$RESULT_FILE" 2>/dev/null || true)
        [ -z "$FINAL" ] && FINAL="Очистка завершена."
        zenity --info --title="Готово ✅" --width=420 --text="$FINAL"
        ;;
    2)
        zenity --warning --title="Завершено с ошибками ⚠️" --width=520 \
            --text="Некоторые команды завершились с ошибкой.\n\n$(echo "$LOG" | grep -i 'ошибка' | head -20)"
        ;;
    *)
        zenity --error --title="Ошибка ❌" --width=520 \
            --text="Скрипт завершился с кодом $EXIT_CODE.\n\n$(echo "$LOG" | tail -30)"
        ;;
esac