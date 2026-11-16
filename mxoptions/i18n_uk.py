"""Ukrainian catalogue."""

STRINGS = {
    # window, tray
    "Quit": "Вийти",
    "Open {}": "Відкрити {}",
    "Not connected": "Не підключено",
    "Connected": "Підключено",
    "charging": "заряджається",
    "Charging · {}%": "Заряджається · {}%",
    "Fully charged": "Повністю заряджено",
    "No mouse": "Миші немає",
    "{} is running too. Two programs that set the same mouse up undo each other's work: quit it while you use MX Options.":
        "{} теж працює. Дві програми, що налаштовують ту саму мишу, скасовують роботу одна одної: закрийте її на час "
        "роботи з MX Options.",
    " and ": " і ",
    "On Windows its service (OptionsPlusUpdaterService) starts it again unless that is stopped as well.":
        "У Windows її служба (OptionsPlusUpdaterService) запускає її знову, якщо не зупинити й службу.",
    "logiops is a system service: sudo systemctl disable --now logid":
        "logiops — системна служба: sudo systemctl disable --now logid",
    "Looking for your mouse… Turn it on and make sure its receiver is plugged in.":
        "Шукаю мишу… Увімкніть її та перевірте, чи вставлено ресівер.",
    "{} is asleep or out of range. Move it to wake it up; your settings are applied automatically.":
        "{} спить або поза зоною дії. Порухайте нею, щоб розбудити, — налаштування застосуються самі.",
    "Buttons": "Кнопки",
    "Point & scroll": "Вказівник і прокрутка",
    "Haptics": "Вібровідгук",
    "Actions Ring": "Кільце дій",
    "Device & app": "Пристрій і програма",

    # buttons page
    "Pick a button to choose what it does.": "Виберіть кнопку, щоб призначити їй дію.",
    "Middle button": "Середня кнопка",
    "Wheel mode button": "Кнопка режиму колеса",
    "Gesture button": "Кнопка жестів",
    "Forward button": "Кнопка «Вперед»",
    "Back button": "Кнопка «Назад»",
    "Haptic panel": "Сенсорна панель",
    "Press the scroll wheel.": "Натискання на колесо прокрутки.",
    "The button behind the scroll wheel. Default switches between ratchet and free spin.":
        "Кнопка за колесом. Типово перемикає покрокову та вільну прокрутку.",
    "Hold it and move the mouse, or just tap it.": "Утримуйте й рухайте мишею або просто натисніть.",
    "The pressure-sensitive pad under your thumb.": "Чутлива до натиску панель під великим пальцем.",

    # action picker
    "Search actions": "Пошук дій",
    "Custom": "Власні",
    "Keyboard shortcut": "Комбінація клавіш",
    "Run command": "Виконати команду",
    "e.g. firefox or a script path": "напр. firefox або шлях до скрипта",
    "Press the shortcut…": "Натисніть комбінацію…",
    "Click to record a shortcut": "Клацніть, щоб записати комбінацію",
    "Back": "Назад",
    "Tap": "Натискання",
    "Swipe up": "Рух угору",
    "Swipe down": "Рух униз",
    "Swipe left": "Рух ліворуч",
    "Swipe right": "Рух праворуч",

    # categories
    "Mouse": "Миша",
    "Desktop": "Робочий стіл",
    "Windows": "Вікна",
    "Editing": "Редагування",
    "Browser": "Браузер",
    "Media": "Медіа",
    "System": "Система",
    "Easy-Switch": "Easy-Switch",

    # actions
    "Default": "Типово",
    "Middle click": "Середній клік",
    "Forward": "Вперед",
    "Gestures": "Жести",
    "Switch wheel mode": "Перемкнути режим колеса",
    "Switch pointer speed": "Перемкнути швидкість вказівника",
    "Precision mode (hold)": "Точний режим (утримання)",
    "Do nothing": "Нічого не робити",
    "Overview": "Огляд",
    "All windows": "Усі вікна",
    "Desktop grid": "Сітка стільниць",
    "Show desktop": "Показати стільницю",
    "Desktop to the left": "Стільниця ліворуч",
    "Desktop to the right": "Стільниця праворуч",
    "Desktop above": "Стільниця вище",
    "Desktop below": "Стільниця нижче",
    "Switch window": "Перемкнути вікно",
    "Application launcher": "Меню програм",
    "Search or run": "Пошук і запуск",
    "Close window": "Закрити вікно",
    "Maximize window": "Розгорнути вікно",
    "Minimize window": "Згорнути вікно",
    "Fullscreen": "На весь екран",
    "Tile window left": "Вікно ліворуч",
    "Tile window right": "Вікно праворуч",
    "Keep window on top": "Вікно поверх інших",
    "Copy": "Копіювати",
    "Paste": "Вставити",
    "Cut": "Вирізати",
    "Undo": "Скасувати",
    "Redo": "Повторити",
    "Select all": "Виділити все",
    "Find": "Знайти",
    "Save": "Зберегти",
    "Delete": "Видалити",
    "Enter": "Enter",
    "Escape": "Escape",
    "New tab": "Нова вкладка",
    "Close tab": "Закрити вкладку",
    "Reopen closed tab": "Відновити закриту вкладку",
    "Next tab": "Наступна вкладка",
    "Previous tab": "Попередня вкладка",
    "Reload": "Оновити",
    "Zoom in": "Збільшити",
    "Zoom out": "Зменшити",
    "Reset zoom": "Скинути масштаб",
    "Play / Pause": "Відтворення / пауза",
    "Next track": "Наступний трек",
    "Previous track": "Попередній трек",
    "Volume up": "Гучніше",
    "Volume down": "Тихіше",
    "Mute": "Вимкнути звук",
    "Mute microphone": "Вимкнути мікрофон",
    "Brightness up": "Яскравіше",
    "Brightness down": "Темніше",
    "Screenshot (region)": "Знімок екрана (область)",
    "Screenshot (full screen)": "Знімок екрана (весь екран)",
    "Screenshot (window)": "Знімок екрана (вікно)",
    "Lock screen": "Заблокувати екран",
    "Clipboard history": "Історія буфера обміну",
    "Emoji picker": "Емодзі",
    "File manager": "Файловий менеджер",
    "Terminal": "Термінал",
    "Calculator": "Калькулятор",
    "Do not disturb": "Не турбувати",
    "Night light": "Нічне світло",
    "Easy-Switch 1": "Easy-Switch 1",
    "Easy-Switch 2": "Easy-Switch 2",
    "Easy-Switch 3": "Easy-Switch 3",
    "Move to the other computer (Flow)": "Перейти на інший комп'ютер (Flow)",

    # point & scroll
    "Pointer speed and how the wheels behave.": "Швидкість вказівника та поведінка коліс.",
    "Pointer": "Вказівник",
    "Pointer speed": "Швидкість вказівника",
    "Second speed": "Друга швидкість",
    "Precision speed": "Точна швидкість",
    "Used by the \"Switch pointer speed\" button action.": "Для дії кнопки «Перемкнути швидкість вказівника».",
    "Used while a \"Precision mode\" button is held.": "Діє, поки утримується кнопка «Точний режим».",
    "Scroll wheel": "Колесо прокрутки",
    "Scroll direction": "Напрямок прокрутки",
    "Standard": "Звичайний",
    "Natural": "Природний",
    "SmartShift": "SmartShift",
    "Spin fast and the wheel releases into free spin on its own.":
        "Крутніть швидше — і колесо саме перейде у вільне обертання.",
    "SmartShift sensitivity": "Чутливість SmartShift",
    "Shifts early": "Раніше",
    "Shifts late": "Пізніше",
    "Wheel mode": "Режим колеса",
    "Used when SmartShift is off.": "Діє, коли SmartShift вимкнено.",
    "Ratchet": "Покроковий",
    "Free spin": "Вільний",
    "Ratchet force": "Сила кроку",
    "Soft": "М'яко",
    "Firm": "Туго",
    "Smooth scrolling": "Плавна прокрутка",
    "High-resolution scrolling. The wheel is then handled by this app, so it only scrolls while the app runs.":
        "Прокрутка високої роздільності. Колесо тоді обробляє ця програма, тож воно працює лише поки вона запущена.",
    "Scroll speed": "Швидкість прокрутки",
    "Thumb wheel": "Бічне колесо",
    "Function": "Функція",
    "Reverse direction": "Зворотний напрямок",
    "Speed": "Швидкість",
    "Horizontal scroll": "Горизонтальна прокрутка",
    "Zoom": "Масштаб",
    "Volume": "Гучність",
    "Switch tabs": "Перемикання вкладок",
    "Switch desktops": "Перемикання стільниць",
    "Brightness": "Яскравість",
    "Custom keystrokes": "Власні комбінації клавіш",
    "Turning up": "Обертання вгору",
    "Turning down": "Обертання вниз",
    "Record": "Записати",
    "Clear": "Очистити",
    "Record the keystroke for turning up": "Записати комбінацію для обертання вгору",
    "Record the keystroke for turning down": "Записати комбінацію для обертання вниз",
    "Clear the keystroke for turning up": "Очистити комбінацію для обертання вгору",
    "Clear the keystroke for turning down": "Очистити комбінацію для обертання вниз",
    "At most 20 keystrokes per second. Fast turns may skip steps; skipped steps do not run later.":
        "Щонайбільше 20 комбінацій за секунду. Швидке обертання може пропускати кроки; пропущені кроки не виконуються пізніше.",

    # haptics
    "Vibration feedback from the thumb panel.": "Вібровідгук сенсорної панелі.",
    "Feedback": "Відгук",
    "Haptic feedback": "Вібровідгук",
    "Battery saving": "Економія заряду",
    "At {percent}% battery or less the mouse does not vibrate, not even for patterns you try or for games; "
    "above {percent}% it does again. What the mouse does by itself, as when it is switched on, stays.":
        "При заряді {percent}% або менше миша не вібрує, навіть для візерунків, які ви пробуєте, чи для ігор; "
        "понад {percent}% вона знову вібрує. Те, що миша робить сама, як-от під час увімкнення, не змінюється.",
    "Vibration is off now to save the battery.": "Зараз вібрацію вимкнено, щоб зберегти заряд.",
    "Intensity": "Інтенсивність",
    "Subtle": "Ледь чутно",
    "Low": "Слабко",
    "Medium": "Середньо",
    "High": "Сильно",
    "Fine tune": "Точне налаштування",
    "Feedback for actions": "Відгук на дії",
    "A tick for gestures, the Actions Ring and mode changes.": "Короткий імпульс для жестів, кільця дій і зміни режимів.",
    "Switching screens": "Перемикання екранів",
    "A short tick when the pointer moves onto another screen of this computer. Moving to another computer with Flow does "
    "not count.":
        "Короткий імпульс, коли вказівник переходить на інший екран цього комп'ютера. Перехід на інший комп'ютер через Flow "
        "не враховується.",
    "This desktop does not tell programs which screen the pointer is on.":
        "Цей робочий стіл не повідомляє програмам, на якому екрані вказівник.",
    "Press force": "Сила натиску",
    "How hard you have to press the thumb panel.": "Наскільки сильно треба тиснути на панель.",
    "Light": "Легко",
    "Try a pattern": "Спробуйте візерунок",
    "Game rumble": "Вібрація з ігор",
    "Games vibrate game controllers, not mice. With this on, MX Options adds a virtual gamepad to the system; what a "
    "game sends to it is played on the mouse. A game then sees a controller: some show gamepad prompts because of "
    "it, and some only vibrate a controller that is being played with. Switch it off if a game misbehaves.":
        "Ігри вібрують геймпадами, а не мишами. Якщо це ввімкнено, MX Options додає в систему віртуальний геймпад; "
        "те, що гра надсилає йому, відтворюється на миші. Гра при цьому бачить контролер: деякі через це показують "
        "підказки для геймпада, а деякі вібрують лише тим контролером, яким грають. Вимкніть, якщо гра поводиться дивно.",
    "Windows lets a program add a game controller only through a driver made for that. Install ViGEmBus once ({}), then "
    "switch this on.":
        "Windows дає програмі додати ігровий контролер лише через спеціальний драйвер. Встановіть ViGEmBus один раз ({}), "
        "потім увімкніть це.",
    "The part of this program that talks to the controller driver (ViGEmClient.dll) is missing here.":
        "Тут бракує частини програми, що говорить із драйвером контролера (ViGEmClient.dll).",
    "Vibrate with games": "Вібрувати разом з іграми",
    "Weak rumble": "Слабка вібрація",
    "Medium rumble": "Середня вібрація",
    "Strong rumble": "Сильна вібрація",
    "Pulse every": "Імпульс кожні",
    "{:.0f} ms": "{:.0f} мс",
    "The motor plays short patterns, not a hum: a rumble that lasts is a row of them, this far apart.":
        "Мотор відтворює короткі візерунки, а не гудіння: тривала вібрація — це їхня низка з таким проміжком.",
    "Sensitivity": "Чутливість",
    "Higher, and a weaker rumble from the game already counts as medium or strong.":
        "Що вище, то слабша вібрація з гри вже вважається середньою чи сильною.",
    "Make it noticed": "Щоб геймпад помітили",
    "Move its stick": "Ворухнути стіком",
    "A gamepad tester in a browser, and some games, take no notice of a controller until something on it moves. This "
    "moves the virtual gamepad's left stick for a moment. Press it while the tester or the game is open.":
        "Тестер геймпадів у браузері та деякі ігри не помічають контролер, доки на ньому щось не ворухнеться. Ця "
        "кнопка на мить відхиляє лівий стік віртуального геймпада. Натискайте, коли тестер чи гра відкриті.",
    "A rumble that grows from weak to strong, as a game would send it.":
        "Вібрація, що наростає від слабкої до сильної, як її надіслала б гра.",
    "This mouse has no haptic motor.": "У цієї миші немає вібромотора.",
    "Sharp state change": "Різка зміна стану",
    "Damp state change": "М'яка зміна стану",
    "Sharp collision": "Різкий удар",
    "Damp collision": "М'який удар",
    "Subtle collision": "Легкий удар",
    "Whisper collision": "Ледь чутний удар",
    "Happy alert": "Радісний сигнал",
    "Angry alert": "Сердитий сигнал",
    "Completed": "Готово",
    "Square": "Квадрат",
    "Wave": "Хвиля",
    "Firework": "Феєрверк",
    "Mad": "Лють",
    "Knock": "Стукіт",
    "Jingle": "Передзвін",
    "Ringing": "Дзвінок",

    # ring
    "Bubbles around your cursor. A bubble runs an action or opens its own ring of bubbles.":
        "Бульбашки довкола курсора. Бульбашка виконує дію або відкриває власне кільце бульбашок.",
    "Press the button and the ring opens around the cursor. Click a bubble, or keep the button held, "
    "point at a bubble and let go. Over a dial, turn the wheel or the thumb wheel.":
        "Натисніть кнопку — кільце відкриється довкола курсора. Клацніть бульбашку або тримайте кнопку, "
        "наведіть на бульбашку й відпустіть. Над регулятором крутіть колесо або бокове коліщатко.",
    "The ring opens with": "Кільце відкриває",
    "Gestures move to the other thumb button.": "Жести переїжджають на іншу кнопку під великим пальцем.",
    "Bubbles": "Бульбашки",
    "Pie": "Пиріг",
    "Tiles": "Плитки",
    "Dial": "Регулятор",
    "For volume, brightness, zoom and the like choose Dial: one bubble that the scroll wheel turns both ways, like a slider.":
        "Для гучності, яскравості, масштабу тощо оберіть «Регулятор»: одна бульбашка, яку колесо крутить в обидва боки, "
        "як слайдер.",
    "Dials": "Регулятори",
    "Point at the bubble and turn the scroll wheel. The ring stays open while you adjust.":
        "Наведіть на бульбашку й крутіть колесо. Кільце лишається відкритим, поки ви регулюєте.",
    "Tracks": "Треки",
    "Seek": "Перемотування",
    "Undo / redo": "Скасувати / повторити",
    "Back / forward": "Назад / вперед",
    "Save the current ring as a preset…": "Зберегти поточне кільце як пресет…",
    "Delete a saved preset": "Видалити збережений пресет",
    "Name for the preset:": "Назва пресету:",
    "Stop this bubble being a folder? The {} bubbles inside it will be removed.":
        "Зробити бульбашку не текою? Бульбашки всередині ({}) буде видалено.",
    "Presets": "Пресети",
    "Everyday": "Щоденний",
    "Windows and desktops": "Вікна і стільниці",
    "Screenshot": "Знімок екрана",
    "Replace the whole ring with the \"{}\" preset? Your current bubbles will be lost.":
        "Замінити все кільце пресетом «{}»? Поточні бульбашки буде втрачено.",
    "No button opens the ring right now. Assign it on the Buttons page.":
        "Зараз жодна кнопка не відкриває кільце. Призначте його на сторінці «Кнопки».",
    "Ring": "Кільце",
    "Ring size": "Розмір кільця",
    "Bubble {}": "Бульбашка {}",
    "Icon and colour": "Іконка і колір",
    "Action": "Дія",
    "Folder": "Тека",
    "Open folder": "Відкрити теку",
    "This bubble opens its own ring. Bubbles inside: {}.": "Ця бульбашка відкриває власне кільце. Бульбашок усередині: {}.",
    "Move one place back": "Пересунути на місце назад",
    "Move one place forward": "Пересунути на місце вперед",
    "Remove this bubble": "Видалити бульбашку",
    "Emoji, letters or an icon name": "Емодзі, літери або назва іконки",
    "Image…": "Зображення…",
    "Automatic": "Авто",
    "Choose an image": "Виберіть зображення",
    "Images": "Зображення",

    # script and macro
    "Script": "Скрипт",
    "Macro": "Макрос",
    "Name": "Назва",
    "Cancel": "Скасувати",
    "Run now": "Запустити зараз",
    "Test in 3 s": "Тест через 3 с",
    "e.g. Toggle VPN": "напр. Перемкнути VPN",
    "e.g. Sign and send": "напр. Підписати й надіслати",
    "Runs with bash. Start with a #! line (for example #!/usr/bin/env python3) to use another interpreter.":
        "Виконується в bash. Почніть із рядка #! (наприклад #!/usr/bin/env python3), щоб узяти інший інтерпретатор.",
    "{name} · {count} lines. Click \"Script\" to edit.": "{name} · рядків: {count}. Клацніть «Скрипт», щоб змінити.",
    "Empty macro. Click \"Macro\" to edit.": "Порожній макрос. Клацніть «Макрос», щоб змінити.",
    "Steps": "Кроки",
    "Add": "Додати",
    "No steps yet. Add the first one below.": "Кроків ще немає. Додайте перший нижче.",
    "Repeat while the button is held": "Повторювати, поки кнопку затиснуто",
    "Only applies when the macro is assigned to a mouse button.": "Діє лише коли макрос призначено на кнопку миші.",
    "Press again to stop": "Зупиняти повторним натисканням",
    "Pressing its mouse button or key again while it plays stops it, instead of playing it once more. Not for gestures, Actions Ring bubbles or other programs.":
        "Повторне натискання його кнопки миші чи клавіші, поки він виконується, зупиняє його замість повторного запуску. Не діє для жестів, бульбашок кільця дій та інших програм.",
    "Keys": "Клавіші",
    "Text": "Текст",
    "Wait": "Пауза",
    "Click": "Клік",
    "Command": "Команда",
    "Text to insert": "Текст для вставлення",
    "Choose an action": "Виберіть дію",
    "Left": "Лівий",
    "Right": "Правий",
    "Middle": "Середній",
    "Left click": "Лівий клік",
    "Right click": "Правий клік",

    # appearance
    "Appearance": "Вигляд",
    "Theme": "Тема",
    "Accent colour": "Колір акценту",
    "Custom…": "Свій…",
    "Graphite": "Графіт",
    "Midnight": "Північ",
    "Black": "Чорна",
    "Daylight": "Світла",
    "Mouse model": "Модель миші",
    "How the mouse is drawn on the Buttons page.": "Як намальовано мишу на сторінці «Кнопки».",
    "Photo": "Фото",
    "3D": "3D",
    "Mouse colour": "Колір миші",
    "Pale grey": "Світло-сіра",
    "Low-poly": "Лоу-полі",
    "Outline": "Контурна",
    "Wireframe": "Каркасна",

    # device & app
    "Move the mouse to another paired computer. It disconnects from this one until you switch back "
    "with the button on its underside.":
        "Перемкнути мишу на інший спарений комп'ютер. Від цього вона від'єднається, доки не перемкнете назад "
        "кнопкою знизу.",
    "Switch the mouse to channel {}? It will stop working on this computer until you switch it back.":
        "Перемкнути мишу на канал {}? Вона не працюватиме на цьому комп'ютері, доки не перемкнете назад.",
    "Paired": "Спарено",
    "Empty": "Порожньо",
    "Application": "Програма",
    "Start at login": "Запускати при вході",
    "Runs quietly in the system tray.": "Тихо працює в системному лотку.",
    "Show in the application menu": "Показувати в меню програм",
    "Language": "Мова",
    "Auto": "Авто",
    "Takes effect after restarting the app.": "Набуде чинності після перезапуску програми.",
    "Reset all settings": "Скинути всі налаштування",
    "Restore defaults": "Відновити типові",
    "Reset every setting to its default?": "Скинути всі налаштування до типових?",
    # the "Pro" ring and the tiling dials
    "Pro": "Профі",
    "Apps": "Програми",
    "Files": "Файли",
    "Tiling": "Тайлінг",
    "Floating": "Плаваюча",
    "Three columns": "Три колонки",
    "Monocle": "Монокль",
    "Tile": "Плитка",
    "Float all": "Усі плаваючі",
    "Float window": "Плаваюче вікно",
    "All layouts": "Усі розкладки",
    "Capture": "Знімки й запис",
    "Record region": "Запис області",
    "Record screen": "Запис екрана",
    "Keyboard layout": "Розкладка клавіатури",
    "System monitor": "Монітор системи",
    "System settings": "Параметри системи",
    "More dials": "Ще регулятори",
    "Window to another desktop": "Вікно на іншу стільницю",
    "Tiling: layout": "Тайлінг: розкладка",
    "Tiling: width": "Тайлінг: ширина",
    "Tiling: focus": "Тайлінг: фокус",

    # the wheel changing mode, thumb wheel steps
    "Mode switch click": "Клацання перемикання режиму",
    "When the wheel changes mode": "Коли колесо змінює режим",
    "One short tick as SmartShift lets the wheel go and as it catches again, like the clutch of earlier MX Master mice.":
        "Один короткий тік, коли SmartShift відпускає колесо і коли воно знову чіпляється, як механізм у попередніх MX Master.",
    "Which moments": "У які моменти",
    "A quick stroke that ends at once gets a single tick either way.":
        "Швидкий рух, який одразу закінчується, у будь-якому разі дає лише один тік.",
    "Letting go and stopping": "Зрив і зупинка",
    "Letting go": "Лише зрив",
    "Stopping": "Лише зупинка",
    "Tick": "Тік",
    "Clack": "Клац",
    "Quieter": "Тихше",
    "Louder": "Гучніше",
    "Tone": "Тон",
    "Lower": "Нижче",
    "Higher": "Вище",
    "Length": "Тривалість",
    "Shorter": "Коротше",
    "Longer": "Довше",
    "Vibration": "Вібрація",
    "Sound": "Звук",
    "Both": "Обидва",
    "Into free spin": "У вільний хід",
    "Back to ratchet": "Назад у храповик",
    "Relay": "Реле",
    "Soft click": "М'який клац",
    "Your own file…": "Свій файл…",
    "Choose a sound": "Виберіть звук",
    "Sounds (*.wav)": "Звуки (*.wav)",
    "Feel the steps": "Відчутні кроки",
    "This wheel turns smoothly, so each step of volume, tabs, desktops, brightness, zoom or keystrokes gets a tick.":
        "Це коліщатко крутиться плавно, тому кожен крок гучності, вкладок, стільниць, яскравості, масштабу чи комбінацій "
        "клавіш дає короткий тик.",

    # firmware
    # shapes drawn with a gesture button
    "Shapes": "Фігури",
    "{} with an action": "з дією: {}",
    "None yet": "Поки жодної",
    "No action": "Без дії",
    "Hold the button and draw: a corner, there and back, a circle, a shake. A shape that has an action here runs it "
    "when the button comes up. While any shape has one, plain swipes of this button are also judged when it comes up, "
    "not half-way; with repeating swipes shapes are not read at all.":
        "Затисніть кнопку й намалюйте: кут, туди й назад, коло, струс. Фігура, якій тут призначено дію, виконує її, "
        "коли кнопку відпущено. Поки хоч одна фігура має дію, звичайні жести цієї кнопки теж оцінюються після "
        "відпускання, а не на півдорозі; з повторюваними жестами фігури не читаються взагалі.",
    "Down, then right": "Униз, потім праворуч",
    "Down, then left": "Униз, потім ліворуч",
    "Up, then right": "Угору, потім праворуч",
    "Up, then left": "Угору, потім ліворуч",
    "Right, then down": "Праворуч, потім униз",
    "Right, then up": "Праворуч, потім угору",
    "Left, then down": "Ліворуч, потім униз",
    "Left, then up": "Ліворуч, потім угору",
    "Up and back": "Угору й назад",
    "Down and back": "Униз і назад",
    "Left and back": "Ліворуч і назад",
    "Right and back": "Праворуч і назад",
    "Circle, clockwise": "Коло за годинниковою",
    "Circle, anticlockwise": "Коло проти годинникової",
    "Shake sideways": "Струс убік",
    "Shake up and down": "Струс угору-вниз",
    "Shape: {}": "Фігура: {}",
    "A shape with no action": "Фігура без дії",

    # Flow
    "Flow": "Flow",
    "Click a place next to this computer to put the other one there. It follows by itself.":
        "Клацніть місце поруч із цим комп'ютером, щоб поставити туди інший. Той підлаштується сам.",
    "This computer": "Цей комп'ютер",
    "The other one": "Інший",
    "the other computer": "іншим комп'ютером",
    "Flow needs Python 3.13 or newer on this computer.": "Для Flow на цьому комп'ютері потрібен Python 3.13 або новіший.",
    "Pair this computer with another one to begin.": "Щоб почати, спаруйте цей комп'ютер з іншим.",
    "Flow is off.": "Flow вимкнено.",
    "Cannot listen for the other computer: {}": "Не вдається чекати на інший комп'ютер: {}",
    "{} is not answering right now: the network between the two stalls, or it is asleep. The mouse stays here until "
    "it answers.":
        "{} зараз не відповідає: мережа між ними підвисає або він спить. Миша лишається тут, доки він не відповість.",
    "Looking for {}…": "Шукаю зв'язок з {}…",
    "Connected to {}. It has not seen the mouse yet: switch the mouse to it once with the Easy-Switch button, and it "
    "will know its channel from then on.":
        "З'єднано з {}. Він ще не бачив мишу: перемкніть мишу на нього один раз кнопкою Easy-Switch — відтоді він "
        "знатиме свій канал.",
    "Connected to {}. This computer has not seen the mouse yet: switch the mouse to it once with the Easy-Switch "
    "button, and it will know its channel from then on.":
        "З'єднано з {}. Цей комп'ютер ще не бачив мишу: перемкніть мишу на нього один раз кнопкою Easy-Switch — "
        "відтоді він знатиме свій канал.",
    "Connected to {name}, but both computers believe they are on Easy-Switch channel {channel}. Switch the mouse to "
    "each of them once with the Easy-Switch button.":
        "З'єднано з {name}, але обидва комп'ютери вважають, що вони на каналі Easy-Switch {channel}. Перемкніть мишу "
        "на кожен із них по разу кнопкою Easy-Switch.",
    "Connected to {}. This desktop does not let a program watch the screen edge: here, give a button, a gesture or a "
    "ring bubble the action “Move to the other computer (Flow)”.":
        "З'єднано з {}. Цей робочий стіл не дає програмі стежити за краєм екрана: тут призначте кнопці, жесту або "
        "бульбашці кільця дію «Перейти на інший комп'ютер (Flow)».",
    "Connected to {}. Push the pointer past the lit edge to cross. A line one pixel wide along that edge is what feels "
    "the pointer here; a click on it is passed on to what is under it.":
        "З'єднано з {}. Щоб перейти, проштовхніть вказівник за підсвічений край. Вказівник тут відчуває лінія в один "
        "піксель уздовж цього краю; клік по ній передається тому, що під нею.",
    "Connected to {}. Push the pointer past the lit edge to cross.":
        "З'єднано з {}. Щоб перейти, проведіть вказівник за підсвічений край.",

    "One mouse on two or three computers. Push the pointer past the edge of the screen and the mouse moves on to the "
    "computer behind it, together with what you copied: text, pictures and files. Every computer runs MX Options, "
    "they are on the same network, and the mouse is paired with each on an Easy-Switch channel of its own.":
        "Одна миша на два чи три комп'ютери. Проведіть вказівник за край екрана — і миша перейде на комп'ютер, що "
        "стоїть за ним, разом зі скопійованим: текстом, зображеннями й файлами. На кожному комп'ютері працює MX "
        "Options, вони в одній мережі, а миша спарена з кожним на своєму каналі Easy-Switch.",
    "Click one of the other computers, then the place next to this one where it stands.":
        "Клацніть один з інших комп'ютерів, а потім місце біля цього, де він стоїть.",
    "One computer shows a six-digit code, you type it on the other, and the two agree on a key that everything "
    "between them is encrypted with. The code itself is never sent, and three wrong tries withdraw it. A computer "
    "can be paired with two others, one on each side.":
        "Один комп'ютер показує шестизначний код, ви вводите його на іншому, і вони домовляються про ключ, яким "
        "шифрується все між ними. Сам код мережею не передається, а після трьох хибних спроб він скасовується. "
        "Комп'ютер можна спарувати з двома іншими, по одному з кожного боку.",
    "Forget {}? To use Flow with it again the two have to be paired anew.":
        "Забути {}? Щоб знову користуватись Flow із ним, їх доведеться спарувати наново.",
    "Copy them in the file manager, cross, and paste. They are sent over when you cross and wait on the other computer "
    "for a day. Until they have all arrived, the clipboard there stays as it was.":
        "Скопіюйте їх у файловому менеджері, перейдіть і вставте. Вони надсилаються під час переходу й чекають на "
        "іншому комп'ютері добу. Поки всі не дійшли, буфер обміну там лишається яким був.",
    "Vibration when the files have arrived": "Вібрація, коли файли дійшли",
    "Vibration when they could not be brought over": "Вібрація, коли їх не вдалося перенести",
    "Address of {}": "Адреса {}",
    "Pairing": "Спарювання",
    "Paired with {}": "Спаровано з {}",
    "Connected, everything between the two is encrypted.": "З'єднано, усе між ними зашифровано.",
    "Not connected right now.": "Зараз не з'єднано.",
    "Forget": "Забути",
    "Paused": "Призупинено",
    "Use {} with Flow": "Використовувати {} у Flow",
    "Paused. It stays paired: switch it on to use it with Flow again.":
        "Призупинено. Спарювання лишається: увімкніть, щоб знову користуватися ним у Flow.",
    "{} is paused: from now on the mouse does not cross to it or back, and nothing copied goes along; a switch "
    "already under way still finishes. The mouse's Easy-Switch button still switches to it.":
        "{} призупинено: відтепер миша не переходить на нього й назад, а скопійоване не передається; перемикання, "
        "що вже почалося, завершиться. Кнопка Easy-Switch на миші й далі перемикає на нього.",
    "Pair with another computer": "Спарувати з іншим комп'ютером",
    "Press this on one of the two computers. On the other, open this page: it finds this computer by itself and asks "
    "for the code.":
        "Натисніть це на одному з двох комп'ютерів. На іншому відкрийте цю сторінку: він сам знайде цей комп'ютер "
        "і спитає код.",
    "Show a code": "Показати код",
    "Type this code on the other computer": "Введіть цей код на іншому комп'ютері",
    "On the other computer, open Flow in MX Options: this computer is listed there. The code is valid for {seconds} "
    "more seconds; wrong tries left: {left}.":
        "На іншому комп'ютері відкрийте Flow в MX Options: цей комп'ютер там є у списку. Код дійсний ще {seconds} с; "
        "хибних спроб лишилось: {left}.",
    "{} shows a pairing code": "{} показує код спарювання",
    "Type the six digits it shows.": "Введіть шість цифр, які він показує.",
    "Pair": "Спарувати",
    "That was not the code. Look at the other computer again.": "Код не той. Гляньте на інший комп'ютер ще раз.",
    "The other computer no longer shows a code. Press “Show a code” there again.":
        "Інший комп'ютер уже не показує код. Натисніть там «Показати код» ще раз.",
    "The other computer did not answer. Is a firewall in the way?": "Інший комп'ютер не відповів. Чи не заважає фаєрвол?",
    "This computer's address: {}": "Адреса цього комп'ютера: {}",

    "Crossing": "Перехід",
    "Push past the edge": "Продавити за край",
    "How far the mouse has to keep moving once the pointer is at the edge. A longer push keeps the pointer on this "
    "computer when you only aim at something near the edge.":
        "Скільки ще треба вести мишу, коли вказівник уже біля краю. Що довше, то менше шансів проскочити на інший "
        "комп'ютер, коли ви просто цілитесь у щось біля краю.",
    "At a touch": "Від дотику",
    "Firmly": "З натиском",
    "Keep the corners free": "Не чіпати кути",
    "This much of the edge, at each of its ends, does not lead across, so that the corners of the screen go on doing "
    "what they do. On the picture above these are the dark ends of the lit edge.":
        "Така частка краю з кожного його кінця не веде на інший комп'ютер, тож кути екрана працюють як завжди. "
        "На малюнку вище це темні кінці підсвіченого краю.",
    "Cross while a full-screen window is in front": "Переходити, коли попереду повноекранне вікно",
    "Off, a game or a film in full screen keeps the mouse however hard you push.":
        "Якщо вимкнено, гра чи фільм на весь екран не відпустять мишу, хоч як тисни.",
    "Cross only while Ctrl is held": "Переходити лише із затиснутою Ctrl",
    "Hold either Ctrl key while pushing past the edge, so that only reaching the edge keeps the mouse here. "
    "The action “Move to the other computer (Flow)” and the Easy-Switch buttons do not need it.":
        "Тримайте будь-яку з клавіш Ctrl, коли продавлюєте за край, тож сам лише край лишає мишу тут. "
        "Дії «Перейти на інший комп'ютер (Flow)» і кнопкам Easy-Switch це не потрібно.",
    "This desktop does not tell MX Options whether Ctrl is held: while this is on, the edge keeps the "
    "mouse here. The action “Move to the other computer (Flow)” still sends it.":
        "Цей робочий стіл не каже MX Options, чи затиснуто Ctrl: поки це ввімкнено, край лишає мишу тут. "
        "Дія «Перейти на інший комп'ютер (Flow)» усе одно її переводить.",
    "This desktop does not tell MX Options whether Ctrl is held, so this cannot be used here.":
        "Цей робочий стіл не каже MX Options, чи затиснуто Ctrl, тож тут цим не скористатися.",
    "Mouse follows the keyboard": "Миша йде за клавіатурою",
    "Switch the keyboard to one of these computers with its Easy-Switch key, and the mouse goes along. "
    "Only between computers running MX Options; this is not Logitech's Easy-Switch of Options+.":
        "Перемкніть клавіатуру на один із цих комп'ютерів її клавішею Easy-Switch, і миша піде слідом. "
        "Лише між комп'ютерами з MX Options; це не Easy-Switch від Logitech в Options+.",
    "Keyboard follows the mouse": "Клавіатура йде за мишею",
    "Send the mouse to one of these computers and the keyboard goes along, when no key is held. Works "
    "from Linux with a Bluetooth keyboard; not yet tried with a real keyboard. Windows can receive the "
    "keyboard but cannot send it on; use the keyboard's Easy-Switch key there.":
        "Перешліть мишу на один із цих комп'ютерів, і клавіатура піде слідом, якщо жодна клавіша не натиснута. "
        "Працює з Linux із клавіатурою на Bluetooth; зі справжньою клавіатурою ще не перевірено. Windows може "
        "прийняти клавіатуру, але не передати її далі; там скористайтеся клавішею Easy-Switch клавіатури.",

    "What goes along": "Що переходить разом із мишею",
    "Copied text and pictures": "Скопійований текст і зображення",
    "What you copied on one computer can be pasted on the other after crossing.":
        "Скопійоване на одному комп'ютері можна вставити на іншому після переходу.",
    "Copied files and folders": "Скопійовані файли й теки",
    "Only text can cross here. For files and pictures install wl-clipboard.":
        "Тут переходить лише текст. Для файлів і зображень встановіть wl-clipboard.",
    "This desktop does not let a program in the background see what was copied. Install wl-clipboard, then start "
    "MX Options again.":
        "Цей робочий стіл не дає програмі у фоні бачити скопійоване. Встановіть wl-clipboard і запустіть MX Options "
        "знову.",
    "Largest amount in one go": "Найбільший обсяг за раз",
    "More than this stays behind, so that a huge folder left on the clipboard is not sent by accident.":
        "Більше за це лишається, щоб випадково не відправити величезну теку, яка просто була в буфері.",
    "No limit": "Без обмеження",
    "Sending {count} file(s) to {name}": "Надсилаю файли ({count}) на {name}",
    "Receiving {count} file(s) from {name}": "Отримую файли ({count}) з {name}",
    "{done} of {total}": "{done} з {total}",
    "{}/s": "{}/с",
    "less than a minute left": "лишилось менше хвилини",
    "about {} min left": "лишилось близько {} хв",
    "about {hours} h {minutes} min left": "лишилось близько {hours} год {minutes} хв",

    "Setting it up by hand": "Налаштування вручну",
    "For networks where the computers cannot find each other: give one of them the other's address, and both the same "
    "key. Each listens on TCP port {port}; finding each other and pairing by code also use UDP port {port} and TCP "
    "port {pair}.":
        "Для мереж, де комп'ютери не можуть знайти один одного: вкажіть одному з них адресу іншого, а обом — той "
        "самий ключ. Кожен слухає TCP-порт {port}; для пошуку один одного й спарювання за кодом ще потрібні UDP-порт "
        "{port} і TCP-порт {pair}.",
    "Address of the other computer": "Адреса іншого комп'ютера",
    "Key": "Ключ",
    "The same on both computers. Pairing by code sets it for you.":
        "Однаковий на обох комп'ютерах. Спарювання за кодом задає його саме.",
    "Generate": "Згенерувати",
    "Replace the key? The other computer will need the new one before Flow works again.":
        "Замінити ключ? Flow запрацює знову, лише коли інший комп'ютер отримає новий.",

    "Firmware": "Прошивка",
    "Check for updates": "Перевірити оновлення",
    "Install fwupd, the system's firmware updater, to see the firmware versions and check for updates.":
        "Встановіть fwupd, системний засіб оновлення прошивок, щоб бачити версії прошивки й перевіряти оновлення.",
    "Asking fwupd…": "Запитую fwupd…",
    "fwupd, the system's firmware updater, does not see the mouse right now.":
        "fwupd, системний засіб оновлення прошивок, зараз не бачить мишу.",
    "{name}: version {version} is available. Install it with the command below, and keep the "
    "mouse switched on and in range until it has finished.":
        "{name}: доступна версія {version}. Встановіть її командою нижче і тримайте мишу ввімкненою та поруч, "
        "доки оновлення не завершиться.",
    "No newer firmware has been published for Linux for these devices.":
        "Новішої прошивки для цих пристроїв під Linux не опубліковано.",
    "Updates are installed by fwupd, the system's firmware updater, from the firmware Logitech "
    "publishes for Linux. MX Options does not flash anything itself.":
        "Оновлення встановлює fwupd, системний засіб оновлення прошивок, із прошивок, які Logitech публікує "
        "для Linux. Сама MX Options нічого не прошиває.",

    # integrations
    "Tray icon": "Іконка в треї",
    "The plain ones take the colour of your panel. They turn faint while no mouse is connected and get a red mark "
    "when its battery is low; “Battery level” fills up with the charge.":
        "Прості беруть колір вашої панелі. Вони бліднуть, коли миша не підключена, і отримують червону позначку, "
        "коли заряд низький; «Рівень заряду» заповнюється разом із батареєю.",
    "Solid": "Заливка",
    "MX Master": "MX Master",
    "Battery level": "Рівень заряду",
    "App icon": "Іконка програми",
    "Integrations": "Інтеграції",
    "Let other programs use the mouse": "Дозволити іншим програмам користуватися мишею",
    "Scripts and apps can play vibrations, run actions, show their own ring, read and change settings "
    "and follow buttons, gestures and the wheel over D-Bus. See API.md in the documentation.":
        "Скрипти й програми можуть через D-Bus запускати вібрації та дії, показувати власне кільце, читати й "
        "змінювати налаштування та стежити за кнопками, жестами й колесом. Опис — у файлі API.md у документації.",
    "Scripts and apps can play vibrations, run actions, show their own ring and read and change settings with "
    "“mxoptions --call”. See API.md in the documentation.":
        "Скрипти й програми можуть вмикати вібрацію, виконувати дії, показувати власне кільце, читати й змінювати "
        "налаштування командою «mxoptions --call». Див. API.md у документації.",
    "written by Claude, Anthropic's AI model": "написано Claude, ШІ-моделлю Anthropic",
    "Signal to another program": "Сигнал іншій програмі",
    "a name that program listens for": "назва, яку чекає та програма",

    # the picture of the mouse
    "Download": "Завантажити",
    "Downloading the pictures…": "Завантажую картинки…",
    "Could not download the pictures: {}": "Не вдалося завантажити картинки: {}",
    "Logitech's own picture of the mouse can be shown here instead of this model. It is not included "
    "with the program: download it from logitech.com (about 2.5 MB).":
        "Замість цієї моделі тут можна показувати справжню картинку миші від Logitech. Вона не постачається "
        "з програмою: завантажте її з logitech.com (близько 2,5 МБ).",

    # the two main buttons
    "Left button": "Ліва кнопка",
    "Right button": "Права кнопка",
    "The mouse handles its two main buttons itself and does not let any software take them over, "
    "so this one cannot be given another action.":
        "Дві основні кнопки миша обробляє сама й не дає жодній програмі їх перехопити, "
        "тому призначити цій кнопці іншу дію не можна.",
    "The other way round works: any other button can be made a left or right click in its own list.":
        "Навпаки можна: будь-яку іншу кнопку можна зробити лівим або правим кліком у її власному списку.",

    # gestures page
    "Hold the gesture button and move the mouse. Here you set how that movement is read.":
        "Тримайте кнопку жестів і ведіть мишу. Тут налаштовується, як цей рух розпізнається.",
    "Try it": "Спробуйте",
    "Hold the gesture button and move the mouse.": "Затисніть кнопку жестів і поведіть мишу.",
    "No button does gestures right now.": "Зараз жести не призначено на жодну кнопку.",
    "Practice mode": "Режим тренування",
    "While this page is open, gestures are only drawn here and do nothing.":
        "Поки відкрита ця сторінка, жести лише малюються тут і нічого не виконують.",
    "Change what the gestures do…": "Змінити дії жестів…",
    "Recognition": "Розпізнавання",
    "When a swipe fires": "Коли спрацьовує жест",
    "On release": "Після відпускання",
    "At once": "Одразу",
    "Repeating": "З повтором",
    "The swipe is judged as a whole when you let go of the button. The most accurate.":
        "Рух оцінюється цілком, коли ви відпускаєте кнопку. Найточніший варіант.",
    "A long, clear swipe runs straight away; anything shorter or less clear is judged when you let go.":
        "Довгий і чіткий рух спрацьовує одразу; коротший або менш чіткий оцінюється після відпускання.",
    "Like \"At once\", and the action runs again as you keep moving: handy for volume or switching desktops.":
        "Як «Одразу», але дія повторюється, поки ви ведете мишу далі: зручно для гучності чи перемикання стільниць.",
    "Shortest swipe": "Найкоротший жест",
    "Move less than this and it counts as a press.": "Менший рух вважається звичайним натисканням.",
    "Sensitive": "Чутливо",
    "Calm": "Спокійно",
    "{:.1f} mm": "{:.1f} мм",
    "{:.0f} mm": "{:.0f} мм",
    "Swipe that fires at once": "Жест, що спрацьовує одразу",
    "A clear swipe this long runs without waiting for you to let go. The dashed circle.":
        "Чіткий рух такої довжини спрацьовує, не чекаючи відпускання. На схемі це пунктирне коло.",
    "Repeat every": "Повтор кожні",
    "Keep moving and the action runs again after this much travel.":
        "Ведіть далі — і дія повториться після такого відрізка.",
    "Diagonal dead zone": "Мертва зона діагоналей",
    "A swipe this close to a diagonal is ignored instead of guessed.":
        "Рух настільки близько до діагоналі ігнорується, а не вгадується.",
    "Off": "Вимк.",
    "Strict": "Суворо",
    "Sideways or up and down": "Убік чи вгору-вниз",
    "Gives one pair of directions more room, if swipes keep landing in the other.":
        "Дає одній парі напрямків більше місця, якщо жести постійно потрапляють в іншу.",
    "Up / down": "Вгору / вниз",
    "Sideways": "Убік",
    "Vibration during gestures": "Вібрація під час жестів",
    "Choose what you feel at each moment of a gesture. ▶ plays the pattern on the mouse.":
        "Виберіть, що відчувається в кожен момент жесту. ▶ програє візерунок на миші.",
    "Feedback for actions is switched off on the Haptics page, so nothing will be felt.":
        "«Відгук на дії» вимкнено на сторінці «Вібровідгук», тому нічого не відчуватиметься.",
    "Ready-made set": "Готовий набір",
    "Quiet": "Тихий",
    "Normal": "Звичайний",
    "Rich": "Виразний",
    "The movement became a swipe": "Рух став жестом",
    "The direction changed": "Змінився напрямок",
    "Swipe recognised": "Жест розпізнано",
    "Every repeat": "Кожен повтор",
    "Not accepted": "Не зараховано",
    "No vibration": "Без вібрації",
    "Play": "Програти",
    "A pattern of its own for each direction": "Свій візерунок для кожного напрямку",
    "Tell the directions apart without looking.": "Щоб розрізняти напрямки, не дивлячись на екран.",
    "Reset the gesture settings": "Скинути налаштування жестів",
    "Button": "Кнопка",
    "Gestures are on": "Жести на кнопці",
    "The Actions Ring moves to the other thumb button.": "Кільце дій переїжджає на іншу кнопку під великим пальцем.",
    "Cancelled: moved away and came back": "Скасовано: рух туди й назад",
    "Ignored: too diagonal ({:.0f}° off)": "Проігноровано: по діагоналі (кут {:.0f}°)",
    "{:.0f}° off": "кут {:.0f}°",

    "No access to /dev/uinput: keyboard shortcuts, clicks and smooth scrolling cannot be sent. "
    "Add a udev rule granting your user access to uinput and log in again.":
        "Немає доступу до /dev/uinput: комбінації клавіш, кліки та плавна прокрутка не працюватимуть. "
        "Додайте правило udev із доступом до uinput для вашого користувача та увійдіть знову.",

    # the keyboard
    "Keyboard": "Клавіатура",
    "Battery": "Акумулятор",
    "Key {}": "Клавіша {}",
    "Fn lock": "Фіксація Fn",
    "Fn lock key": "Клавіша фіксації Fn",
    "F-Lock": "F-Lock",
    "Backlight down": "Підсвітка −",
    "Backlight up": "Підсвітка +",
    "Kept by the keyboard": "За клавіатурою",
    "MX Options leaves the Easy-Switch keys and the keys of the Fn layer to the keyboard, so that switching computers "
    "and the Fn keys always work.":
        "MX Options залишає клавіші Easy-Switch і клавіші шару Fn самій клавіатурі, щоб перемикання комп'ютерів "
        "і клавіші Fn працювали завжди.",
    "Changed by another program": "Змінено іншою програмою",
    "Another program has changed what this key sends, so MX Options leaves it alone.":
        "Інша програма змінила те, що надсилає ця клавіша, тож MX Options її не чіпає.",
    "Changed already": "Уже змінено",
    "This key was already changed when the keyboard was found, and MX Options cannot tell by whom, so it leaves the key alone.":
        "Ця клавіша вже була змінена, коли клавіатуру знайдено, і MX Options не може визначити ким, тож її не чіпає.",
    "Other software for Logitech devices is running, so the keys of the keyboard are left as they are.":
        "Працює інша програма для пристроїв Logitech, тож клавіші клавіатури залишено як є.",
    "This keyboard does not say which one it is, so its keys are left as they are.":
        "Ця клавіатура не повідомляє, яка саме вона, тож її клавіші залишено як є.",
    "The record of changed keys cannot be read: {}. Keys are left as they are.":
        "Не вдається прочитати запис змінених клавіш: {}. Клавіші залишено як є.",
    "The record of changed keys already lists 16 other keyboards, so the keys of this one are left as they are.":
        "Запис змінених клавіш уже містить 16 інших клавіатур, тож клавіші цієї залишено як є.",
    "The keyboard was not moved to the other computer: a key MX Options changed could not be given back first. "
    "Its keys work as set here; try again in a moment.":
        "Клавіатуру не перемкнуто на інший комп’ютер: не вдалося спершу повернути клавішу, змінену MX Options. "
        "Її клавіші працюють, як налаштовано тут; спробуйте ще раз за мить.",
    "Pick a key to choose what it does. A key left at Default does what it always does; the keys you change go back "
    "to normal when MX Options quits.":
        "Виберіть клавішу, щоб задати, що вона робить. Клавіша з дією «Типово» робить те, що й завжди; змінені клавіші "
        "повертаються до звичайної роботи, коли MX Options завершує роботу.",
    "The keyboard has not said yet how this key reports; the action is applied once it does.":
        "Клавіатура ще не повідомила, як працює ця клавіша; дію буде застосовано, щойно вона це зробить.",
    "Turn the keyboard on to see its keys here. Its receiver can be the one of the mouse.":
        "Увімкніть клавіатуру, щоб побачити тут її клавіші. Вона може працювати через ресівер миші.",
    "Press an Easy-Switch key to move the keyboard to the computer on that channel. Hold one for three seconds to pair "
    "the keyboard with another computer on it.":
        "Натисніть клавішу Easy-Switch, щоб перевести клавіатуру на комп'ютер цього каналу. Утримуйте її три секунди, "
        "щоб спарувати клавіатуру з іншим комп'ютером на цьому каналі.",
    "fwupd, the system's firmware updater, does not see the keyboard right now.":
        "fwupd, системний засіб оновлення прошивок, зараз не бачить клавіатуру.",
    "Firmware {}": "Прошивка {}",
    "Read from the keyboard. fwupd, the system's firmware updater, does not see it right now.":
        "Прочитано з клавіатури. fwupd, системний засіб оновлення прошивок, зараз її не бачить.",
    "Settings the keyboard controls itself. Changes are made on the keyboard. Its own shortcuts may change them too.":
        "Налаштування, якими клавіатура керує сама. Зміни робляться на клавіатурі. Її власні сполучення клавіш теж "
        "можуть їх змінювати.",
    "Use F1–F12 without Fn": "F1–F12 без Fn",
    "On: the top row gives F1–F12, and Fn with a key gives its other function. Off: the other way round. The "
    "keyboard's own Fn lock shortcut changes it too.":
        "Увімкнено: верхній ряд дає F1–F12, а Fn разом із клавішею дає її іншу функцію. Вимкнено: навпаки. "
        "Власне сполучення клавіатури для фіксації Fn теж це змінює.",
    "The keyboard did not say how this is set.": "Клавіатура не повідомила, як це налаштовано.",
    "The keyboard does not let MX Options change this.": "Клавіатура не дозволяє MX Options це змінювати.",
    "The keyboard did not take the change.": "Клавіатура не прийняла зміну.",
    "The keyboard did not take the change as asked; the earlier setting was put back.":
        "Клавіатура прийняла зміну не так, як просили; попереднє налаштування повернуто.",
    "The keyboard did not confirm the change. Shown as the keyboard reports it.":
        "Клавіатура не підтвердила зміну. Показано так, як повідомляє клавіатура.",
    "The keyboard can swap whether the top row gives F1–F12 or its other functions without Fn, as its guide "
    "describes. MX Options does not change this yet.":
        "Клавіатура може міняти, що дає верхній ряд без Fn: F1–F12 чи свої інші функції, як описано в її посібнику. "
        "MX Options поки цього не змінює.",
    "Backlight": "Підсвітка",
    # keyboard pages: cards, Easy-Switch channels, key groups, the names of keys
    "Typing": "Друк",
    "Firmware & reset": "Прошивка і скидання",
    "Channel {}: {}": "Канал {}: {}",
    "Left as they are": "Залишено як є",
    "General": "Загальне",
    "Fn": "Fn",
    "Screen brightness down": "Яскравість екрана нижче",
    "Screen brightness up": "Яскравість екрана вище",
    "Task view": "Перегляд завдань",
    "Action center": "Центр дій",
    "Play/Pause": "Відтворення/пауза",
    "Dictation": "Диктування",
    "Emoji": "Емодзі",
    "Print Screen": "Print Screen",
    "Search": "Пошук",
    "Switch app": "Перемкнути програму",
    "View open apps": "Відкриті програми",
    "View all apps": "Усі програми",
    "Language switch": "Зміна мови",
    "AI prompt": "Запит до ШІ",
    "Other keys": "Інші клавіші",
    "Grave accent": "Гравіс",
    "Tab": "Tab",
    "Caps Lock": "Caps Lock",
    "Left Shift": "Лівий Shift",
    "Left Ctrl": "Лівий Ctrl",
    "Left Start/Option": "Лівий Start/Option",
    "Left Alt/Command": "Лівий Alt/Command",
    "Right Alt/Command": "Правий Alt/Command",
    "Right Start/Option": "Правий Start/Option",
    "Right Ctrl": "Правий Ctrl",
    "Right Shift": "Правий Shift",
    "Insert": "Insert",
    "Home": "Home",
    "End": "End",
    "Page Up": "Page Up",
    "Page Down": "Page Down",
    "Backslash": "Зворотна скісна риска",
    "The keyboard has a backlight with settings of its own, set on the keyboard as its guide describes. MX Options "
    "does not change them yet.":
        "Клавіатура має підсвітку з власними налаштуваннями, які задають на самій клавіатурі, як описано в її "
        "посібнику. MX Options поки їх не змінює.",
    "Backlight on": "Підсвітку ввімкнено",
    "The keyboard keeps these settings itself; its own backlight keys change them too. MX Options does not store them.":
        "Клавіатура зберігає ці налаштування сама; її власні клавіші підсвітки теж їх змінюють. MX Options їх не зберігає.",
    "MX Options does not change the backlight of this keyboard; its own keys do.":
        "MX Options не змінює підсвітку цієї клавіатури; це роблять її власні клавіші.",
    "Brightness is set with the keyboard's keys just now. Choose Automatic or Fixed to set it here.":
        "Зараз яскравість задано клавішами клавіатури. Виберіть «Авто» або «Стала», щоб задати її тут.",
    "Brightness is set with the keyboard's keys.": "Яскравість задають клавішами клавіатури.",
    "The keyboard uses a brightness setting MX Options does not know; it is left as it is.":
        "Клавіатура використовує налаштування яскравості, якого MX Options не знає; його залишено як є.",
    "Brightness and timing are set with the keyboard's keys; newer keyboard firmware may let MX Options set them too.":
        "Яскравість і час задають клавішами клавіатури; новіша прошивка клавіатури може дозволити MX Options задавати "
        "їх теж.",
    "Fixed": "Стала",
    "Automatic brightness": "Автоматична яскравість",
    "Fixed brightness": "Стала яскравість",
    "Brightness level": "Рівень яскравості",
    "Backlight brightness level": "Рівень яскравості підсвітки",
    "Dimmer": "Тьмяніше",
    "Brighter": "Яскравіше",
    "Steps 0 to {top}, as the keyboard counts them. In Automatic, the light around the keyboard sets the brightness.":
        "Кроки від 0 до {top}, як їх лічить клавіатура. В режимі «Авто» яскравість задає світло навколо клавіатури.",
    "Off after, hands away": "Вимкнення, руки далеко",
    "Backlight off after, hands away": "Вимкнення підсвітки, руки далеко",
    "How long the backlight stays on after you type, once your hands move away.":
        "Скільки підсвітка світить після набору, коли руки відійшли від клавіатури.",
    "Off after, hands near": "Вимкнення, руки поруч",
    "Backlight off after, hands near": "Вимкнення підсвітки, руки поруч",
    "How long the backlight stays on after you type, while your hands stay near the keyboard.":
        "Скільки підсвітка світить після набору, поки руки лишаються поруч із клавіатурою.",
    "Off after, plugged in": "Вимкнення, під час заряджання",
    "Backlight off after, plugged in": "Вимкнення підсвітки під час заряджання",
    "How long the backlight stays on after you type, while the keyboard is charging over USB.":
        "Скільки підсвітка світить після набору, поки клавіатура заряджається через USB.",
    "Not known": "Невідомо",
    "{} h": "{} год",
    "{} min": "{} хв",
    "{} s": "{} с",
    "Off at low battery": "Вимкнення при низькому заряді",
    "Shown as the keyboard reports it. MX Options does not change this yet.":
        "Показано так, як повідомляє клавіатура. MX Options поки цього не змінює.",
    "Show a notice when the brightness changes on the keyboard": "Показувати сповіщення, коли яскравість змінюється на клавіатурі",
    "Keyboard backlight: level {level} of {top}": "Підсвітка клавіатури: рівень {level} з {top}",
    "Operating system layout": "Розкладка для операційної системи",
    "The keyboard can be set up for the layout of another operating system, on the keyboard itself as its guide "
    "describes. MX Options does not change this yet.":
        "Клавіатуру можна налаштувати на розкладку іншої операційної системи на самій клавіатурі, як описано в її "
        "посібнику. MX Options поки цього не змінює.",
    "Disable keys": "Вимкнення клавіш",
    "The keyboard can turn some of its keys off. MX Options does not do this yet.":
        "Клавіатура може вимикати деякі свої клавіші. MX Options поки цього не робить.",
    "Windows/Super key": "Клавіша Windows/Super",
    "Turn off the {} key": "Вимкнути клавішу {}",
    "Turn off keys you press by mistake. A key turned off does nothing; turn its switch off to enable it again.":
        "Вимкніть клавіші, які натискаєте випадково. Вимкнена клавіша нічого не робить; щоб знову її ввімкнути, "
        "вимкніть її перемикач.",
    "The keyboard does this itself. Whether it keeps it after it is switched off is not known yet; MX Options shows "
    "what the keyboard reports.":
        "Клавіатура робить це сама. Чи зберігає вона це після вимкнення, поки невідомо; MX Options показує те, що "
        "повідомляє клавіатура.",
    "Layout {}": "Розкладка {}",
    "The layout the keyboard uses for the operating system on this Easy-Switch channel; each channel keeps its own. "
    "The keyboard's own shortcut for it, as its guide describes, changes it too.":
        "Розкладка, яку клавіатура використовує для операційної системи на цьому каналі Easy-Switch; кожен канал має "
        "свою. Власне сполучення клавіш клавіатури для неї, як описано в її посібнику, теж її змінює.",
    "Lock keys": "Клавіші фіксації",
    "The keyboard reports when Caps Lock, Num Lock or Scroll Lock turns on or off. MX Options can show a notice then; "
    "it never saves or logs their state.":
        "Клавіатура повідомляє, коли Caps Lock, Num Lock чи Scroll Lock вмикається чи вимикається. MX Options може тоді "
        "показати сповіщення; стан цих клавіш ніколи не зберігається й не записується в журнал.",
    "Show a notice when a lock key turns on or off": "Показувати сповіщення, коли клавіша фіксації вмикається чи вимикається",
    "Show a notice when Fn lock changes on the keyboard": "Показувати сповіщення, коли фіксація Fn змінюється на клавіатурі",
    "Fn lock on: the top row gives F1–F12": "Фіксацію Fn увімкнено: верхній ряд дає F1–F12",
    "Fn lock off: the top row gives its other functions": "Фіксацію Fn вимкнено: верхній ряд дає інші функції",
    "{key} on": "{key} увімкнено",
    "{key} off": "{key} вимкнено",
    "Every key does what it always does": "Кожна клавіша робить те, що й завжди",
    "Restore keyboard defaults": "Відновити типові налаштування клавіатури",
    "Give every key of the keyboard back its own function, in every app? The keys set for single apps go too.":
        "Повернути всім клавішам клавіатури їхні власні функції в усіх програмах? Клавіші, задані для окремих програм, теж зникнуть.",
    "Keys for": "Клавіші для",
    "All apps": "Усі програми",
    "Add app": "Додати програму",
    "Remove": "Вилучити",
    "Remove the keys of this app": "Вилучити клавіші цієї програми",
    "Remove the keys of {}? In that app the keys then do what they do in every app.":
        "Вилучити клавіші {}? У цій програмі клавіші робитимуть те саме, що й в усіх інших.",
    "As in all apps": "Як в усіх програмах",
    "This desktop does not tell other programs which app is in front, so keys of their own for one app cannot "
    "work here. The keys do what is set for all apps.":
        "Це робоче середовище не повідомляє іншим програмам, яка програма зараз попереду, тому окремі клавіші для "
        "однієї програми тут не працюють. Клавіші роблять те, що задано для всіх програм.",
    "While {} is in front. \"As in all apps\": the key does what it does in every app. \"Default\": the key does "
    "what it always does, here, even where it is changed for all apps.":
        "Поки попереду {}. «Як в усіх програмах»: клавіша робить те саме, що й в усіх програмах. «Типово»: клавіша "
        "робить те, що й завжди, тут, навіть якщо для всіх програм її змінено.",
    "To give an app keys of its own, add it: {} was in front last.":
        "Щоб дати програмі власні клавіші, додайте її: останньою попереду була {}.",
    "To give an app keys of its own, switch to it, then come back here and add it.":
        "Щоб дати програмі власні клавіші, перейдіть до неї, потім поверніться сюди й додайте її.",
    "The battery of {name} is low ({percent}%). Charge it soon.":
        "Акумулятор {name} майже розряджений ({percent}%). Зарядіть його найближчим часом.",
    "Export…": "Експортувати…",
    "Import…": "Імпортувати…",
    "Settings file": "Файл налаштувань",
    "Every setting in one file, your commands and scripts included: keep it private. The pairings of Flow stay on "
    "this computer.":
        "Усі налаштування в одному файлі, разом із вашими командами й скриптами: не показуйте його іншим. "
        "Пари Flow лишаються на цьому комп’ютері.",
    "Export settings": "Експорт налаштувань",
    "Import settings": "Імпорт налаштувань",
    "Settings (*.json)": "Налаштування (*.json)",
    "The settings are in {}.": "Налаштування збережено у {}.",
    "Replace every setting with the ones in {}? The settings you have now are kept in a backup first, and the "
    "pairings of Flow stay as they are.":
        "Замінити всі налаштування тими, що у {}? Поточні налаштування спершу буде збережено в резервній копії, "
        "а пари Flow лишаться як є.",
    "The settings of {} are in use. A change of language takes effect after restarting the app.":
        "Налаштування з {} застосовано. Зміна мови набуде чинності після перезапуску програми.",
    "It is {size} KB, more than the {limit} KB a settings file may have.":
        "Його розмір {size} КБ, більше за {limit} КБ, дозволені для файлу налаштувань.",
    "It is not a settings file of MX Options.": "Це не файл налаштувань MX Options.",
    "It was made by a newer version of MX Options.": "Його створила новіша версія MX Options.",
    "The settings hold something a settings file cannot.": "Налаштування містять те, чого файл налаштувань містити не може.",
    "Nothing was exported. {}": "Нічого не експортовано. {}",
    "The settings were written, but the system could not confirm that they are safely on the disk ({}).":
        "Налаштування записано, але система не змогла підтвердити, що вони надійно збережені на диску ({}).",
    "The settings could not be written ({}).": "Не вдалося записати налаштування ({}).",
    "{name} cannot be imported. {reason}": "{name} не можна імпортувати. {reason}",
    "{name} could not be read ({reason}).": "Не вдалося прочитати {name} ({reason}).",
    "Export macro": "Експорт макросу",
    "Import macro": "Імпорт макросу",
    "Macro (*.json)": "Макрос (*.json)",
    "The macro is in {}. Everything it types or runs is in the file as written.":
        "Макрос збережено у {}. Усе, що він вводить чи запускає, записано у файлі як є.",
    "The macro was written, but the system could not confirm that it is safely on the disk ({}).":
        "Макрос записано, але система не змогла підтвердити, що він надійно збережений на диску ({}).",
    "The macro could not be written ({}).": "Не вдалося записати макрос ({}).",
    "It is not a macro file of MX Options.": "Це не файл макросу MX Options.",
    "It is {size} KB, more than the {limit} KB a macro file may have.":
        "Його розмір {size} КБ, більше за {limit} КБ, дозволені для файлу макросу.",
    "The macro has no complete step yet.": "У макросі ще немає жодного завершеного кроку.",
    "The macro holds something a macro file cannot.": "Макрос містить те, чого файл макросу містити не може.",
    "Imported from {}. A macro file can run commands: check each step before you save.":
        "Імпортовано з {}. Файл макросу може запускати команди: перевірте кожен крок, перш ніж зберегти.",
    "Nothing was imported: the settings in use could not be kept in a backup first ({}).":
        "Нічого не імпортовано: не вдалося спершу зберегти поточні налаштування в резервній копії ({}).",
    "Nothing was imported: the system could not confirm that the new settings are safely on the disk ({reason}), "
    "and putting the settings in use back in their file failed too ({again}). The file may hold the imported "
    "settings; the settings in use are kept in {backup}.":
        "Нічого не імпортовано: система не змогла підтвердити, що нові налаштування надійно збережені на диску "
        "({reason}), і повернути поточні налаштування до їхнього файлу теж не вдалося ({again}). Файл може містити "
        "імпортовані налаштування; поточні налаштування збережено в {backup}.",
    "Nothing was imported: the system could not confirm that the new settings are safely on the disk ({}). The "
    "settings in use were put back.":
        "Нічого не імпортовано: система не змогла підтвердити, що нові налаштування надійно збережені на диску ({}). "
        "Поточні налаштування повернуто.",
    "Nothing was imported: the settings could not be saved ({}). They are as they were.":
        "Нічого не імпортовано: не вдалося зберегти налаштування ({}). Вони лишилися як були.",
    "Settings for": "Налаштування для",
    "Remove the settings of this app": "Вилучити налаштування цієї програми",
    "Remove the settings of {}? There the mouse then works as in every app.":
        "Вилучити налаштування {}? Там миша працюватиме як в усіх програмах.",
    "This desktop does not tell which app is in front, so settings for one app cannot work here.":
        "Це робоче середовище не повідомляє, яка програма попереду, тому налаштування для однієї програми тут не працюють.",
    "While {} is in front. \"As in all apps\": as set for all apps. \"Default\": the button's own function, even "
    "where all apps change it.":
        "Поки попереду {}. «Як в усіх програмах»: як задано для всіх програм. «Типово»: власна функція кнопки, навіть "
        "якщо для всіх програм її змінено.",
    "To give an app its own settings, add it: {} was in front last.":
        "Щоб дати програмі власні налаштування, додайте її: останньою попереду була {}.",
    "To give an app its own settings, switch to it, then come back and add it.":
        "Щоб дати програмі власні налаштування, перейдіть до неї, потім поверніться й додайте її.",
    "{} uses the ring for all apps.": "{} використовує кільце для всіх програм.",
    "Make a ring for this app": "Створити кільце для цієї програми",
    "Use the ring for all apps": "Використовувати кільце для всіх програм",
    "Use the ring for all apps in {}? Its own ring goes.": "Використовувати в {} кільце для всіх програм? Власне кільце зникне.",
    "Size and style are shared by all apps.": "Розмір і вигляд спільні для всіх програм.",
    "While {} is in front. \"As in all apps\": the function set for all apps. Direction, speed and steps are the "
    "same in every app; the keystrokes can be its own.":
        "Поки попереду {}. «Як в усіх програмах»: функція, задана для всіх програм. Напрямок, швидкість і кроки "
        "однакові в усіх програмах; комбінації клавіш можуть бути власними.",
    "Use the keystrokes for all apps": "Використовувати комбінації для всіх програм",
    "Keystrokes": "Комбінації клавіш",
    "{} has keystrokes of its own.": "{} має власні комбінації клавіш.",
    "As in all apps. Recording or clearing one here gives {} keystrokes of its own.":
        "Як в усіх програмах. Запис чи очищення тут дасть {} власні комбінації клавіш.",
    "Holding it and moving": "Утримання й рух",
    "Swipes": "Напрямки",
    "Pan": "Панорама",
    "Pan (hold and move)": "Панорама (утримати й рухати)",
    "Zoom (hold and move)": "Масштаб (утримати й рухати)",
    "How far the page moves, or how quickly it zooms, for the same movement of the hand.":
        "Наскільки зсувається сторінка або як швидко змінюється масштаб за той самий рух руки.",
    "Each way you move runs its own action, set under \"Change what the gestures do…\".":
        "Кожен напрямок руху виконує власну дію, задану в «Змінити дії жестів…».",
    "While the button is held, the page follows your hand: up, down and sideways. The pointer stays where it is. "
    "A press without moving still runs the tap action.":
        "Поки кнопку утримано, сторінка рухається за рукою: вгору, вниз і вбік. Вказівник лишається на місці. "
        "Натискання без руху й далі виконує дію натискання.",
    "While the button is held, move up to zoom in and down to zoom out, as Ctrl and the wheel do. A press without "
    "moving still runs the tap action.":
        "Поки кнопку утримано, рух угору збільшує масштаб, а вниз зменшує, як Ctrl з колесом. Натискання без руху "
        "й далі виконує дію натискання.",
    # Spotlight effects
    "Spotlight effects": "Ефекти прожектора",
    "Highlight": "Підсвічування",
    "Still Magnify": "Нерухома лупа",
    "Digital Laser": "Цифрова лазерна указка",
    "On": "Увімк.",
    "Show this effect": "Показувати цей ефект",
    "Size": "Розмір",
    "Colour": "Колір",
    "Hold the button: the effect follows the pointer until you let go. With more than one effect on, "
    "double-click and hold for the next one. Clicks and scrolling do nothing while it shows. Still Magnify "
    "shows the screen as it was when you pressed, and not on Wayland desktops.":
        "Утримуйте кнопку: ефект іде за вказівником, доки ви її не відпустите. Якщо ввімкнено кілька ефектів, "
        "двічі клацніть і утримуйте, щоб перейти до наступного. Поки ефект видно, клацання й прокрутка нічого не "
        "роблять. Нерухома лупа показує екран таким, яким він був у мить натискання, і не працює на стільницях Wayland.",
    "Shows the screen as it was when you pressed: a still picture, kept in memory until you let go. "
    "Not a live magnifier.":
        "Показує екран таким, яким він був у мить натискання: нерухомий знімок, що зберігається лише в пам'яті, "
        "доки ви не відпустите кнопку. Це не жива лупа.",
    "Not available on Wayland desktops.": "Недоступно на стільницях Wayland.",
    "Not available on this desktop.": "Недоступно на цій стільниці.",
    # Suggested settings for one app
    "Suggested settings": "Запропоновані налаштування",
    "Web browser": "Веббраузер",
    "Spreadsheet": "Електронна таблиця",
    "Documents and slides": "Документи й слайди",
    "Zoom meetings": "Зустрічі в Zoom",
    "Microsoft Teams meetings": "Зустрічі в Microsoft Teams",
    "Photoshop": "Photoshop",
    "Premiere": "Premiere",
    "Own choice after the Windows shortcuts Adobe documents, not the settings of Logi Options+. Adobe names [ and ] "
    "for the brush size of Pattern Stamp and Spot Healing Brush; for other tools it does not say. With \"Use Legacy "
    "Undo Shortcuts\" on, Ctrl+Z works otherwise. These are key positions of a US keyboard: with another "
    "layout, or shortcuts changed in the app, they may do something else.":
        "Власний вибір за клавішами Windows, які описує Adobe, а не налаштування Logi Options+. Adobe називає [ і ] "
        "для розміру пензля в Pattern Stamp і Spot Healing Brush; про інші інструменти не каже. З увімкненим "
        "«Use Legacy Undo Shortcuts» Ctrl+Z працює інакше. Це місця клавіш американської клавіатури: з іншою "
        "розкладкою чи зміненими в програмі клавішами вони можуть робити інше.",
    "Own choice after the Windows shortcuts Adobe documents, not the settings of Logi Options+. Page Down and Page "
    "Up show the next and the previous screen of the Timeline: a whole screen per step, not smooth scrolling. Adobe "
    "says the pointer must be over the Timeline; its list of shortcuts says the panel must have focus. These "
    "are key positions of a US keyboard: with another layout, or shortcuts changed in the app, they may do "
    "something else.":
        "Власний вибір за клавішами Windows, які описує Adobe, а не налаштування Logi Options+. Page Down і Page Up "
        "показують наступний і попередній екран Timeline: цілий екран за крок, а не плавна прокрутка. Adobe каже, що "
        "вказівник має бути над Timeline; її список клавіш каже, що панель має бути у фокусі. Це місця клавіш "
        "американської клавіатури: з іншою розкладкою чи зміненими в програмі клавішами вони можуть робити інше.",
    "To undo the keystrokes of the thumb wheel, choose \"Use the keystrokes for all apps\" on the Point & scroll "
    "page, before its function.":
        "Щоб скасувати комбінації бічного колеса, виберіть «Використовувати комбінації для всіх програм» на сторінці "
        "«Вказівник і прокрутка», раніше ніж його функцію.",
    "Own choice from Zoom's default shortcuts, not a set of Logi Options+: back sends Alt+A (mute or unmute your "
    "audio), forward Alt+V (start or stop your video), once per press. They work while Zoom is in front and its "
    "shortcuts are unchanged. Keys are key positions: on a layout where A or V sit elsewhere (French, for one), other "
    "shortcuts are sent.":
        "Власний вибір зі стандартних скорочень Zoom, а не набір Logi Options+: «Назад» надсилає Alt+A (вимкнути чи "
        "ввімкнути звук), «Вперед» — Alt+V (увімкнути чи вимкнути відео), раз на натискання. Вони діють, поки Zoom "
        "на передньому плані і його скорочення не змінено. Клавіші — це положення клавіш: на розкладці, де A чи V "
        "стоять деінде (французькій, наприклад), надсилаються інші скорочення.",
    "Own choice from the default shortcuts of the Teams desktop app for Windows, not a set of Logi Options+: back "
    "sends Ctrl+Shift+M (mute or unmute), forward Ctrl+Shift+O (video on or off), once per press. They work while "
    "Teams is in front and its shortcuts are unchanged. Microsoft lists them for the US layout; keys are key "
    "positions.":
        "Власний вибір зі стандартних скорочень програми Teams для Windows, а не набір Logi Options+: «Назад» "
        "надсилає Ctrl+Shift+M (вимкнути чи ввімкнути мікрофон), «Вперед» — Ctrl+Shift+O (увімкнути чи вимкнути "
        "відео), раз на натискання. Вони діють, поки Teams на передньому плані і його скорочення не змінено. "
        "Microsoft наводить їх для розкладки США; клавіші — це положення клавіш.",
    "Apply the suggested settings \"{preset}\" to {app}?": "Застосувати запропоновані налаштування «{preset}» до {app}?",
    "{control}: stays as set for this app ({value})": "{control}: лишається, як задано для цієї програми ({value})",
    "Logi Options+ also pans while the middle button is held here. These settings leave the middle button as it "
    "is: to pan, give it Gestures for this app and choose Pan.":
        "Logi Options+ тут ще й прокручує сторінку, поки утримано середню кнопку. Ці налаштування лишають середню "
        "кнопку як є: щоб прокручувати, дайте їй «Жести» для цієї програми й виберіть «Панорама».",
    "These are the gestures for all apps. Those of {} are set on the Buttons page; how gestures are read and felt "
    "counts in every app.":
        "Це жести для всіх програм. Жести {} задаються на сторінці «Кнопки»; як жести розпізнаються й відчуваються, "
        "однаково в усіх програмах.",
    "Everything else stays as it is. To undo one of these, choose \"As in all apps\" for it.":
        "Усе інше лишається як є. Щоб скасувати щось із цього, виберіть для нього «Як в усіх програмах».",
    "Settings changed while this dialog was open. Choose Suggested settings again.":
        "Налаштування змінилися, поки це вікно було відкрито. Знову виберіть «Запропоновані налаштування».",
    "Settings changed while this dialog was open. Choose the bubble type again.":
        "Налаштування змінилися, поки це вікно було відкрито. Знову виберіть тип бульбашки.",
    "Settings changed while this dialog was open. Choose the preset again.":
        "Налаштування змінилися, поки це вікно було відкрито. Знову виберіть пресет.",
}
