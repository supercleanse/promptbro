"""Microphone selection against fake pactl output (no real audio, no live devices)."""
import json

from promptbro import audiosel

LAPTOP = "alsa_input.pci-0000_00_1f.3-platform-sof_sdw.HiFi__Mic__source"
USB = "alsa_input.usb-Apple_Inc._Studio_Display_0000AAAA-0000-02.mono-fallback"
BT = "bluez_input.00:11:22:33:44:55"
MON = "alsa_output.pci-0000_00_1f.3.analog-stereo.monitor"


def src(name, bus=None):
    props = {"device.bus": bus} if bus else {}
    return {"name": name, "properties": props}


ALL = [src(MON, "pci"), src(LAPTOP, "pci"), src(USB, "usb"), src(BT, "bluetooth")]


def test_default_bluetooth_picks_usb():
    assert audiosel.choose("auto", ALL, BT)[0] == USB


def test_default_usb_kept():
    assert audiosel.choose("auto", ALL, USB)[0] == USB


def test_default_laptop_kept_even_with_usb_present():
    assert audiosel.choose("", ALL, LAPTOP)[0] == LAPTOP


def test_only_laptop_and_bluetooth_picks_laptop():
    assert audiosel.choose("auto", [src(LAPTOP, "pci"), src(BT, "bluetooth"), src(MON)], BT)[0] == LAPTOP


def test_bluetooth_by_prefix_alone_is_skipped():
    assert audiosel.choose("auto", [src(BT), src(LAPTOP)], BT)[0] == LAPTOP


def test_only_bluetooth_means_no_source():
    assert audiosel.choose("auto", [src(BT, "bluetooth"), src(MON)], BT)[0] is None


def test_monitors_never_chosen():
    assert audiosel.choose("auto", [src(MON), src(BT, "bluetooth")], MON)[0] is None


def test_pinned_substring_survives_serial_change():
    other = USB.replace("AAAA", "BBBB")
    assert audiosel.choose("Studio_Display", [src(LAPTOP), src(other, "usb")], LAPTOP)[0] == other


def test_pinned_glob_and_exact():
    assert audiosel.choose("alsa_input.usb-*", ALL, BT)[0] == USB
    assert audiosel.choose(LAPTOP, ALL, USB)[0] == LAPTOP


def test_pinned_missing_falls_back_to_auto():
    name, why = audiosel.choose("Nonexistent_Mic", ALL, BT)
    assert name == USB and "not present" in why


def test_explicit_bluetooth_pin_is_honored():
    assert audiosel.choose("bluez_input", ALL, USB)[0] == BT


def test_select_with_fake_pactl_and_failure():
    def runner(*args):
        return json.dumps(ALL) if args[0] == "-f" else BT + "\n"
    assert audiosel.select("auto", runner)[0] == USB

    def broken(*args):
        raise FileNotFoundError("pactl")
    assert audiosel.select("auto", broken) == (None, "pactl unavailable")
