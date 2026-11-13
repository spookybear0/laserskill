from functools import wraps
import types
import django
import sys
import os

def setup_environment():
    inner_folder = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "laserskill"))

    submodule_package = types.ModuleType("laserskill")
    submodule_package.__path__ = [inner_folder]

    sys.modules["laserskill"] = submodule_package
    if inner_folder not in sys.path:
        sys.path.insert(0, inner_folder)

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "laserskill.settings")
    django.setup()