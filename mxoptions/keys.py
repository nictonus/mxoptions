"""The names of keys and mouse buttons, as settings keep them, and their numbers.

The names are Linux's ("KEY_LEFTCTRL", "KEY_C", "BTN_LEFT": the ones of <linux/input-event-codes.h>),
whatever system the program runs on, so that a settings file means the same everywhere. On Linux the
numbers are what the kernel takes; for Windows there is windows_key() below."""

# name without its "KEY_" -> number. Several names for one number are all kept.
_KEYS = {
    "ESC": 1, "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9, "9": 10, "0": 11, "MINUS": 12, "EQUAL": 13,
    "BACKSPACE": 14, "TAB": 15, "Q": 16, "W": 17, "E": 18, "R": 19, "T": 20, "Y": 21, "U": 22, "I": 23, "O": 24, "P": 25,
    "LEFTBRACE": 26, "RIGHTBRACE": 27, "ENTER": 28, "LEFTCTRL": 29, "A": 30, "S": 31, "D": 32, "F": 33, "G": 34, "H": 35,
    "J": 36, "K": 37, "L": 38, "SEMICOLON": 39, "APOSTROPHE": 40, "GRAVE": 41, "LEFTSHIFT": 42, "BACKSLASH": 43, "Z": 44,
    "X": 45, "C": 46, "V": 47, "B": 48, "N": 49, "M": 50, "COMMA": 51, "DOT": 52, "SLASH": 53, "RIGHTSHIFT": 54,
    "KPASTERISK": 55, "LEFTALT": 56, "SPACE": 57, "CAPSLOCK": 58, "F1": 59, "F2": 60, "F3": 61, "F4": 62, "F5": 63, "F6": 64,
    "F7": 65, "F8": 66, "F9": 67, "F10": 68, "NUMLOCK": 69, "SCROLLLOCK": 70, "KP7": 71, "KP8": 72, "KP9": 73, "KPMINUS": 74,
    "KP4": 75, "KP5": 76, "KP6": 77, "KPPLUS": 78, "KP1": 79, "KP2": 80, "KP3": 81, "KP0": 82, "KPDOT": 83,
    "ZENKAKUHANKAKU": 85, "102ND": 86, "F11": 87, "F12": 88, "RO": 89, "KATAKANA": 90, "HIRAGANA": 91, "HENKAN": 92,
    "KATAKANAHIRAGANA": 93, "MUHENKAN": 94, "KPJPCOMMA": 95, "KPENTER": 96, "RIGHTCTRL": 97, "KPSLASH": 98, "SYSRQ": 99,
    "RIGHTALT": 100, "LINEFEED": 101, "HOME": 102, "UP": 103, "PAGEUP": 104, "LEFT": 105, "RIGHT": 106, "END": 107,
    "DOWN": 108, "PAGEDOWN": 109, "INSERT": 110, "DELETE": 111, "MACRO": 112, "MUTE": 113, "VOLUMEDOWN": 114, "VOLUMEUP": 115,
    "POWER": 116, "KPEQUAL": 117, "KPPLUSMINUS": 118, "PAUSE": 119, "SCALE": 120, "KPCOMMA": 121, "HANGEUL": 122,
    "HANGUEL": 122, "HANJA": 123, "YEN": 124, "LEFTMETA": 125, "RIGHTMETA": 126, "COMPOSE": 127, "STOP": 128, "AGAIN": 129,
    "PROPS": 130, "UNDO": 131, "FRONT": 132, "COPY": 133, "OPEN": 134, "PASTE": 135, "FIND": 136, "CUT": 137, "HELP": 138,
    "MENU": 139, "CALC": 140, "SETUP": 141, "SLEEP": 142, "WAKEUP": 143, "FILE": 144, "SENDFILE": 145, "DELETEFILE": 146,
    "XFER": 147, "PROG1": 148, "PROG2": 149, "WWW": 150, "MSDOS": 151, "COFFEE": 152, "SCREENLOCK": 152, "DIRECTION": 153,
    "ROTATE_DISPLAY": 153, "CYCLEWINDOWS": 154, "MAIL": 155, "BOOKMARKS": 156, "COMPUTER": 157, "BACK": 158, "FORWARD": 159,
    "CLOSECD": 160, "EJECTCD": 161, "EJECTCLOSECD": 162, "NEXTSONG": 163, "PLAYPAUSE": 164, "PREVIOUSSONG": 165,
    "STOPCD": 166, "RECORD": 167, "REWIND": 168, "PHONE": 169, "ISO": 170, "CONFIG": 171, "HOMEPAGE": 172, "REFRESH": 173,
    "EXIT": 174, "MOVE": 175, "EDIT": 176, "SCROLLUP": 177, "SCROLLDOWN": 178, "KPLEFTPAREN": 179, "KPRIGHTPAREN": 180,
    "NEW": 181, "REDO": 182, "F13": 183, "F14": 184, "F15": 185, "F16": 186, "F17": 187, "F18": 188, "F19": 189, "F20": 190,
    "F21": 191, "F22": 192, "F23": 193, "F24": 194, "PLAYCD": 200, "PAUSECD": 201, "PROG3": 202, "PROG4": 203,
    "ALL_APPLICATIONS": 204, "DASHBOARD": 204, "SUSPEND": 205, "CLOSE": 206, "PLAY": 207, "FASTFORWARD": 208,
    "BASSBOOST": 209, "PRINT": 210, "HP": 211, "CAMERA": 212, "SOUND": 213, "QUESTION": 214, "EMAIL": 215, "CHAT": 216,
    "SEARCH": 217, "CONNECT": 218, "FINANCE": 219, "SPORT": 220, "SHOP": 221, "ALTERASE": 222, "CANCEL": 223,
    "BRIGHTNESSDOWN": 224, "BRIGHTNESSUP": 225, "MEDIA": 226, "SWITCHVIDEOMODE": 227, "KBDILLUMTOGGLE": 228,
    "KBDILLUMDOWN": 229, "KBDILLUMUP": 230, "SEND": 231, "REPLY": 232, "FORWARDMAIL": 233, "SAVE": 234, "DOCUMENTS": 235,
    "BATTERY": 236, "BLUETOOTH": 237, "WLAN": 238, "UWB": 239, "UNKNOWN": 240, "VIDEO_NEXT": 241, "VIDEO_PREV": 242,
    "BRIGHTNESS_CYCLE": 243, "BRIGHTNESS_AUTO": 244, "BRIGHTNESS_ZERO": 244, "DISPLAY_OFF": 245, "WIMAX": 246, "WWAN": 246,
    "RFKILL": 247, "MICMUTE": 248, "OK": 352, "SELECT": 353, "GOTO": 354, "CLEAR": 355, "POWER2": 356, "OPTION": 357,
    "INFO": 358, "TIME": 359, "VENDOR": 360, "ARCHIVE": 361, "PROGRAM": 362, "CHANNEL": 363, "FAVORITES": 364, "EPG": 365,
    "PVR": 366, "MHP": 367, "LANGUAGE": 368, "TITLE": 369, "SUBTITLE": 370, "ANGLE": 371, "FULL_SCREEN": 372, "ZOOM": 372,
    "MODE": 373, "KEYBOARD": 374, "ASPECT_RATIO": 375, "SCREEN": 375, "PC": 376, "TV": 377, "TV2": 378, "VCR": 379,
    "VCR2": 380, "SAT": 381, "SAT2": 382, "CD": 383, "TAPE": 384, "RADIO": 385, "TUNER": 386, "PLAYER": 387, "TEXT": 388,
    "DVD": 389, "AUX": 390, "MP3": 391, "AUDIO": 392, "VIDEO": 393, "DIRECTORY": 394, "LIST": 395, "MEMO": 396,
    "CALENDAR": 397, "RED": 398, "GREEN": 399, "YELLOW": 400, "BLUE": 401, "CHANNELUP": 402, "CHANNELDOWN": 403, "FIRST": 404,
    "LAST": 405, "AB": 406, "NEXT": 407, "RESTART": 408, "SLOW": 409, "SHUFFLE": 410, "BREAK": 411, "PREVIOUS": 412,
    "DIGITS": 413, "TEEN": 414, "TWEN": 415, "VIDEOPHONE": 416, "GAMES": 417, "ZOOMIN": 418, "ZOOMOUT": 419, "ZOOMRESET": 420,
    "WORDPROCESSOR": 421, "EDITOR": 422, "SPREADSHEET": 423, "GRAPHICSEDITOR": 424, "PRESENTATION": 425, "DATABASE": 426,
    "NEWS": 427, "VOICEMAIL": 428, "ADDRESSBOOK": 429, "MESSENGER": 430, "BRIGHTNESS_TOGGLE": 431, "DISPLAYTOGGLE": 431,
    "SPELLCHECK": 432, "LOGOFF": 433, "DOLLAR": 434, "EURO": 435, "FRAMEBACK": 436, "FRAMEFORWARD": 437, "CONTEXT_MENU": 438,
    "MEDIA_REPEAT": 439, "10CHANNELSUP": 440, "10CHANNELSDOWN": 441, "IMAGES": 442, "NOTIFICATION_CENTER": 444,
    "PICKUP_PHONE": 445, "HANGUP_PHONE": 446, "LINK_PHONE": 447, "DEL_EOL": 448, "DEL_EOS": 449, "INS_LINE": 450,
    "DEL_LINE": 451, "FN": 464, "FN_ESC": 465, "FN_F1": 466, "FN_F2": 467, "FN_F3": 468, "FN_F4": 469, "FN_F5": 470,
    "FN_F6": 471, "FN_F7": 472, "FN_F8": 473, "FN_F9": 474, "FN_F10": 475, "FN_F11": 476, "FN_F12": 477, "FN_1": 478,
    "FN_2": 479, "FN_D": 480, "FN_E": 481, "FN_F": 482, "FN_S": 483, "FN_B": 484, "FN_RIGHT_SHIFT": 485, "BRL_DOT1": 497,
    "BRL_DOT2": 498, "BRL_DOT3": 499, "BRL_DOT4": 500, "BRL_DOT5": 501, "BRL_DOT6": 502, "BRL_DOT7": 503, "BRL_DOT8": 504,
    "BRL_DOT9": 505, "BRL_DOT10": 506, "NUMERIC_0": 512, "NUMERIC_1": 513, "NUMERIC_2": 514, "NUMERIC_3": 515,
    "NUMERIC_4": 516, "NUMERIC_5": 517, "NUMERIC_6": 518, "NUMERIC_7": 519, "NUMERIC_8": 520, "NUMERIC_9": 521,
    "NUMERIC_STAR": 522, "NUMERIC_POUND": 523, "NUMERIC_A": 524, "NUMERIC_B": 525, "NUMERIC_C": 526, "NUMERIC_D": 527,
    "CAMERA_FOCUS": 528, "WPS_BUTTON": 529, "TOUCHPAD_TOGGLE": 530, "TOUCHPAD_ON": 531, "TOUCHPAD_OFF": 532,
    "CAMERA_ZOOMIN": 533, "CAMERA_ZOOMOUT": 534, "CAMERA_UP": 535, "CAMERA_DOWN": 536, "CAMERA_LEFT": 537,
    "CAMERA_RIGHT": 538, "ATTENDANT_ON": 539, "ATTENDANT_OFF": 540, "ATTENDANT_TOGGLE": 541, "LIGHTS_TOGGLE": 542,
    "ALS_TOGGLE": 560, "ROTATE_LOCK_TOGGLE": 561, "REFRESH_RATE_TOGGLE": 562, "BUTTONCONFIG": 576, "TASKMANAGER": 577,
    "JOURNAL": 578, "CONTROLPANEL": 579, "APPSELECT": 580, "SCREENSAVER": 581, "VOICECOMMAND": 582, "ASSISTANT": 583,
    "KBD_LAYOUT_NEXT": 584, "EMOJI_PICKER": 585, "DICTATE": 586, "CAMERA_ACCESS_ENABLE": 587, "CAMERA_ACCESS_DISABLE": 588,
    "CAMERA_ACCESS_TOGGLE": 589, "ACCESSIBILITY": 590, "DO_NOT_DISTURB": 591, "BRIGHTNESS_MIN": 592, "BRIGHTNESS_MAX": 593,
    "EPRIVACY_SCREEN_ON": 594, "EPRIVACY_SCREEN_OFF": 595, "ACTION_ON_SELECTION": 596, "CONTEXTUAL_INSERT": 597,
    "CONTEXTUAL_QUERY": 598, "KBDINPUTASSIST_PREV": 608, "KBDINPUTASSIST_NEXT": 609, "KBDINPUTASSIST_PREVGROUP": 610,
    "KBDINPUTASSIST_NEXTGROUP": 611, "KBDINPUTASSIST_ACCEPT": 612, "KBDINPUTASSIST_CANCEL": 613, "RIGHT_UP": 614,
    "RIGHT_DOWN": 615, "LEFT_UP": 616, "LEFT_DOWN": 617, "ROOT_MENU": 618, "MEDIA_TOP_MENU": 619, "NUMERIC_11": 620,
    "NUMERIC_12": 621, "AUDIO_DESC": 622, "3D_MODE": 623, "NEXT_FAVORITE": 624, "STOP_RECORD": 625, "PAUSE_RECORD": 626,
    "VOD": 627, "UNMUTE": 628, "FASTREVERSE": 629, "SLOWREVERSE": 630, "DATA": 631, "ONSCREEN_KEYBOARD": 632,
    "PRIVACY_SCREEN_TOGGLE": 633, "SELECTIVE_SCREENSHOT": 634, "NEXT_ELEMENT": 635, "PREVIOUS_ELEMENT": 636,
    "AUTOPILOT_ENGAGE_TOGGLE": 637, "MARK_WAYPOINT": 638, "SOS": 639, "NAV_CHART": 640, "FISHING_CHART": 641,
    "SINGLE_RANGE_RADAR": 642, "DUAL_RANGE_RADAR": 643, "RADAR_OVERLAY": 644, "TRADITIONAL_SONAR": 645, "CLEARVU_SONAR": 646,
    "SIDEVU_SONAR": 647, "NAV_INFO": 648, "BRIGHTNESS_MENU": 649, "MACRO1": 656, "MACRO2": 657, "MACRO3": 658, "MACRO4": 659,
    "MACRO5": 660, "MACRO6": 661, "MACRO7": 662, "MACRO8": 663, "MACRO9": 664, "MACRO10": 665, "MACRO11": 666, "MACRO12": 667,
    "MACRO13": 668, "MACRO14": 669, "MACRO15": 670, "MACRO16": 671, "MACRO17": 672, "MACRO18": 673, "MACRO19": 674,
    "MACRO20": 675, "MACRO21": 676, "MACRO22": 677, "MACRO23": 678, "MACRO24": 679, "MACRO25": 680, "MACRO26": 681,
    "MACRO27": 682, "MACRO28": 683, "MACRO29": 684, "MACRO30": 685, "MACRO_RECORD_START": 688, "MACRO_RECORD_STOP": 689,
    "MACRO_PRESET_CYCLE": 690, "MACRO_PRESET1": 691, "MACRO_PRESET2": 692, "MACRO_PRESET3": 693, "KBD_LCD_MENU1": 696,
    "KBD_LCD_MENU2": 697, "KBD_LCD_MENU3": 698, "KBD_LCD_MENU4": 699, "KBD_LCD_MENU5": 700, "PERFORMANCE": 701,
}
CODES = {f"KEY_{name}": code for name, code in _KEYS.items()}
CODES.update(BTN_LEFT=0x110, BTN_RIGHT=0x111, BTN_MIDDLE=0x112, BTN_SIDE=0x113, BTN_EXTRA=0x114)
# number -> name: of several names the one that comes first in the kernel's list
NAMES = {}
for _name, _code in CODES.items():
    NAMES.setdefault(_code, _name)


def code(name):
    """The number of a key or button name, or None if there is no such name."""
    return CODES.get(name)


def name(code):
    """The name of a key's number, or None."""
    return NAMES.get(code)


# -- Windows -------------------------------------------------------------------------------------
# Windows takes a key as a scan code of the PC keyboard (the key's place, whatever the layout: the same
# idea as Linux's numbers), some with the "extended" mark, or as one of its own virtual-key numbers.
# The first 88 Linux numbers are those scan codes as they stand; the rest are listed.
_EXTENDED = {
    "KPENTER": 0x1C, "RIGHTCTRL": 0x1D, "KPSLASH": 0x35, "SYSRQ": 0x37, "RIGHTALT": 0x38, "HOME": 0x47, "UP": 0x48,
    "PAGEUP": 0x49, "LEFT": 0x4B, "RIGHT": 0x4D, "END": 0x4F, "DOWN": 0x50, "PAGEDOWN": 0x51, "INSERT": 0x52,
    "DELETE": 0x53, "LEFTMETA": 0x5B, "RIGHTMETA": 0x5C, "COMPOSE": 0x5D, "MENU": 0x5D, "POWER": 0x5E,
}
_PLAIN = {"102ND": 0x56, "F11": 0x57, "F12": 0x58, "F13": 0x64, "F14": 0x65, "F15": 0x66, "F16": 0x67, "F17": 0x68,
          "F18": 0x69, "F19": 0x6A, "F20": 0x6B, "F21": 0x6C, "F22": 0x6D, "F23": 0x6E, "F24": 0x76}
_VIRTUAL = {
    "MUTE": 0xAD, "VOLUMEDOWN": 0xAE, "VOLUMEUP": 0xAF, "NEXTSONG": 0xB0, "PREVIOUSSONG": 0xB1, "STOPCD": 0xB2,
    "STOP": 0xB2, "PLAYPAUSE": 0xB3, "PLAYCD": 0xB3, "BACK": 0xA6, "FORWARD": 0xA7, "REFRESH": 0xA8, "SEARCH": 0xAA,
    "BOOKMARKS": 0xAB, "HOMEPAGE": 0xAC, "WWW": 0xAC, "MAIL": 0xB4, "MEDIA": 0xB5, "CALC": 0xB7, "SLEEP": 0x5F,
    "PAUSE": 0x13, "HELP": 0x2F, "PRINT": 0x2A, "ZOOMIN": 0xFB,
}


def windows_key(name):
    """How Windows is told of the key ``name``: ("scan", scan code, extended) or ("vk", virtual-key number);
    None for a key Windows has no word for (the screen brightness keys, for one)."""
    if not name.startswith("KEY_"):
        return None
    short = name[4:]
    if short in _VIRTUAL:
        return ("vk", _VIRTUAL[short])
    if short in _EXTENDED:
        return ("scan", _EXTENDED[short], True)
    if short in _PLAIN:
        return ("scan", _PLAIN[short], False)
    number = CODES.get(name)
    if number is not None and 1 <= number <= 83:
        return ("scan", number, False)
    return None


# What Qt reports for a pressed key on Windows is the scan code, the extended ones with 0xE000 added.
_FROM_WINDOWS = {0xE000 | code: f"KEY_{short}" for short, code in _EXTENDED.items() if short != "MENU"}
_FROM_WINDOWS.update({code: f"KEY_{short}" for short, code in _PLAIN.items()})
_FROM_WINDOWS.update({0xE020: "KEY_MUTE", 0xE02E: "KEY_VOLUMEDOWN", 0xE030: "KEY_VOLUMEUP", 0xE019: "KEY_NEXTSONG",
                      0xE010: "KEY_PREVIOUSSONG", 0xE024: "KEY_STOPCD", 0xE022: "KEY_PLAYPAUSE"})


def from_windows(scan_code):
    """The name of the key with this Windows scan code (as Qt's nativeScanCode gives it), or None."""
    if scan_code & 0xFF00 == 0x0100:  # the other way of marking an extended key
        scan_code = 0xE000 | (scan_code & 0xFF)
    if scan_code in _FROM_WINDOWS:
        return _FROM_WINDOWS[scan_code]
    return NAMES.get(scan_code) if 1 <= scan_code <= 83 else None
