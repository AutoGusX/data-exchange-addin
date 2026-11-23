"""Data Exchange add-in for Fusion: load, refresh and publish Autodesk Data Exchanges.

The add-in owns the UI and the Fusion document. A local .NET helper (helper/FusionDxHelper.exe,
started on demand) owns sign-in and every call to the Autodesk Data Exchange SDK.
"""
import os
import traceback

import adsk.core

from .lib import commands


def run(context):
    try:
        commands.start(os.path.dirname(os.path.abspath(__file__)))
    except Exception:
        adsk.core.Application.get().userInterface.messageBox(
            'Data Exchange add-in failed to start:\n' + traceback.format_exc())


def stop(context):
    try:
        commands.stop()
    except Exception:
        adsk.core.Application.get().userInterface.messageBox(
            'Data Exchange add-in failed to stop cleanly:\n' + traceback.format_exc())
