import os

# The defaults depend on the desktop (environment.py). The tests are written for the one the program grew up on,
# whichever desktop they are run from; test_environment.py covers what the others get.
os.environ["MXOPTIONS_DESKTOP"] = "kde"
os.environ["XDG_SESSION_TYPE"] = "wayland"
