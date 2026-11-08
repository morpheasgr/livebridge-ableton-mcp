"""Surge XT effects: what each FX type's twelve controls (``fxN_p0``..``fxN_p11``) are called,
their ranges in patch units, and the values Surge starts a fresh effect with.

Facts only. The start values and the display at 0 / 0.5 / 1 of every control come from a
survey in Live by local Claude (Surge XT 1.4.0). Unused controls, which show "-", are written
as 0. Names are Surge's own labels as Live shows them, read by local Claude (Surge's manual
helped match them first).
A range is (low, high) in patch units (Live's 0..1 is linear between them), or ("menu",
first, count), or None when unknown.
"""

from __future__ import annotations

import math
from typing import Any

# Slot order in the patch: fx1..fx16 = A1 A2 B1 B2 S1 S2 G1 G2 A3 A4 B3 B4 S3 S4 G3 G4.
FX_SLOTS = ["A1", "A2", "B1", "B2", "S1", "S2", "G1", "G2",
            "A3", "A4", "B3", "B4", "S3", "S4", "G3", "G4"]
# Sends S1-S4 are fed by a_send_fx_1..4 / b_send_fx_1..4.
SEND_OF_SLOT = {5: 1, 6: 2, 13: 3, 14: 4}


def _st(hz: float) -> float:
    """Hz as semitones from 440 Hz (Surge's frequency unit)."""
    return 12 * math.log2(hz / 440.0)


_HZ = (-60.0, 70.0)                 # 13.75 Hz .. 25.1 kHz
_PCT, _BI = (0.0, 1.0), (-1.0, 1.0)
_DB24, _DB48 = (-24.0, 24.0), (-48.0, 48.0)
_TIME = (-8.0, 5.0)                 # log2 seconds: 3.9 ms .. 32 s
_RATE = (-7.0, 9.0)                 # log2 Hz: 0.008 .. 512 Hz
_OCT = (0.0, 5.0)
_AMP = (0.0, 1.0)                   # -inf .. 0 dB on Surge's level curve
_WAVES = ("menu", 0, 6)             # Sine, Triangle, Saw, Noise, S&H, Square

# type -> 12 x (name or None, range)
FX_PARAMS: dict[int, list[tuple[str | None, Any]]] = {
    1: [("Left", _TIME), ("Right", _TIME), ("Feedback", _PCT), ("Crossfeed", _PCT),
        ("Low Cut", _HZ), ("High Cut", _HZ), ("Rate", _RATE), ("Depth", (0.0, 2.0)),
        ("Channel", _BI), (None, None), ("Mix", _PCT), ("Width", _DB24)],          # Delay
    2: [("Pre-Delay", _TIME), ("Room Shape", ("menu", 0, 4)), ("Size", _PCT),
        ("Decay Time", (-4.0, 6.0)), ("HF Damping", _PCT), ("Low Cut", _HZ),
        ("Peak Freq", _HZ), ("Peak Gain", _DB48), ("High Cut", _HZ), ("Mix", _PCT),
        ("Width", _DB24), (None, None)],                                           # Reverb 1
    3: [("Center", _BI), ("Feedback", _BI), ("Sharpness", _BI), ("Rate", _RATE),
        ("Depth", _PCT), ("Stereo", _PCT), ("Mix", _PCT), ("Width", _DB24),
        ("Count", None), ("Spread", _PCT), ("Waveform", None), ("Tone", _BI)],      # Phaser
    4: [("Horn Rate", _RATE), ("Doppler", _PCT), ("Tremolo", _PCT), ("Rotor Rate", (0.0, 2.0)),
        ("Drive", _PCT), ("Model", None), ("Width", _DB24), ("Mix", _PCT),
        (None, None), (None, None), (None, None), (None, None)],                   # Rotary
    5: [("Pre-EQ Gain", _DB48), ("Pre-EQ Frequency", _HZ), ("Pre-EQ Bandwidth", _OCT),
        ("Pre-EQ High Cut", _HZ), ("Drive", _DB24), ("Feedback", _BI), ("Post-EQ Gain", _DB48),
        ("Post-EQ Frequency", _HZ), ("Post-EQ Bandwidth", _OCT), ("Post-EQ High Cut", _HZ),
        ("Output Gain", _DB24), ("Model", None)],                                  # Distortion
    6: [("Gain 1", _DB48), ("Frequency 1", _HZ), ("Bandwidth 1", _OCT),
        ("Gain 2", _DB48), ("Frequency 2", _HZ), ("Bandwidth 2", _OCT),
        ("Gain 3", _DB48), ("Frequency 3", _HZ), ("Bandwidth 3", _OCT),
        ("Gain", _DB48), ("Mix", _BI), (None, None)],                       # EQ
    7: [("Left", None), ("Right", _BI), ("Time", _TIME), ("Feedback", _PCT), ("Mix", _PCT),
        (None, None), (None, None), (None, None), (None, None), (None, None), (None, None),
        (None, None)],                                                             # Freq Shift
    8: [("Bass", (-12.0, 12.0)), ("Treble", (-12.0, 12.0)), ("Width", _BI), ("Balance", _BI),
        ("Threshold", (-48.0, 0.0)), ("Attack Rate", _BI), ("Release Rate", _BI),
        ("Gain", (-48.0, 0.0)), ("Side Low Cut", _HZ), (None, None), (None, None),
        (None, None)],                                                             # Conditioner
    9: [("Time", None), ("Rate", _RATE), ("Depth", _PCT), ("Feedback", _PCT), ("Low Cut", _HZ),
        ("High Cut", _HZ), ("Mix", _PCT), ("Width", _DB24), (None, None), (None, None),
        (None, None), (None, None)],                                               # Chorus
    10: [("Gain", _DB48), ("Gate", (-96.0, 0.0)), ("Env Follow", _PCT), ("Q", _BI),
         (None, None), ("Bands", ("menu", 4, 17)), ("Min Frequency", (-36.0, 36.0)),
         ("Max Frequency", (0.0, 60.0)), ("Input", ("menu", 0, 4)), ("Range", _BI),
         ("Center", _BI), ("Mix", _PCT)],                                          # Vocoder
    11: [("Pre-Delay", (-8.0, 1.0)), ("Room Size", _BI), ("Decay Time", (-4.0, 6.0)),
         ("Diffusion", _PCT), ("Buildup", _PCT), ("Modulation", _PCT), ("LF Damping", _PCT),
         ("HF Damping", _PCT), ("Width", _DB24), ("Mix", _PCT), (None, None),
         (None, None)],                                                            # Reverb 2
    12: [("Mode", ("menu", 0, 4)), ("Waveform", _WAVES), ("Rate", _RATE), ("Depth", _PCT),
         ("Count", (1.0, 4.0)), ("Base Pitch", (0.0, 127.0)), ("Spacing", (0.0, 12.0)),
         ("Feedback", _PCT), ("HF Damping", _PCT), ("Width", _DB24), ("Mix", _BI),
         (None, None)],                                                            # Flanger
    13: [("Shape", None), ("Frequency", (0.0, 127.0)), ("Unison Detune", (0.0, 1.0)),
         ("Unison Voices", ("menu", 1, 16)), ("Forward Bias", _PCT), ("Linear Region", _PCT),
         ("Low Cut", _HZ), ("High Cut", _HZ), ("Mix", _PCT), (None, None), (None, None),
         (None, None)],                                                            # Ring Mod
    14: [("FX", None)] + [(f"Param {k}", None) for k in range(10)]
        + [(None, None)],                                                          # Airwindows
    15: [("Drive", _DB24), ("Squash", _PCT), ("Stab", _PCT), ("Asymmetry", _PCT),
         ("Bias", _PCT), ("Comb Frequency", _HZ), ("Comb Separation", None),
         ("Waveform", _WAVES), ("Rate", _RATE), ("Depth", _PCT),
         ("Width", _DB24), ("Output Gain", _DB24)],                                # Neuron
    16: [(band, _DB24) for band in ("30 Hz", "60 Hz", "120 Hz", "250 Hz", "500 Hz", "1 kHz",
                                    "2 kHz", "4 kHz", "8 kHz", "12 kHz", "16 kHz")]
        + [("Gain", _DB48)],                                                       # Graphic EQ
    17: [("Frequency 1", (_st(60), _st(300))), ("Resonance 1", _PCT), ("Gain 1", _AMP),
         ("Frequency 2", (_st(300), _st(1500))), ("Resonance 2", _PCT), ("Gain 2", _AMP),
         ("Frequency 3", (_st(1500), _st(7500))), ("Resonance 3", _PCT), ("Gain 3", _AMP),
         ("Mode", ("menu", 0, 4)), ("Gain", _DB48), ("Mix", _PCT)],         # Resonator
    18: [("Threshold", (-96.0, 0.0)), ("Ratio", (1.0, 20.0)), ("Flip", ("menu", 0, 2)),
         ("Mix", _PCT)] + [(None, None)] * 8,                                      # CHOW
    19: [("Drive", _PCT), ("Tone", _PCT), ("Attack", _PCT), ("Release", _PCT), ("Mix", _PCT)]
        + [(None, None)] * 7,                                                      # Exciter
    20: [("Anti-Alias Filter", _HZ), ("Frequency 1", (math.log2(0.01), math.log2(20))),
         ("Depth 1", _PCT), ("Frequency 2", (math.log2(0.01), math.log2(20))),
         ("Depth 2", _PCT), ("Delay Type", None), ("Clock Rate", (1.5, 100.0)),
         ("Saturation", _PCT), ("Feedback", _PCT), ("Width", _BI), ("Mix", _PCT),
         ("Reconstruction Filter", _HZ)],                                          # Ensemble
    21: [("Extra Noise", _PCT), ("Center", (_st(0.5), 70.0)), ("Offset 2", (-60.0, 60.0)),
         ("Offset 3", (-60.0, 60.0)), ("Feedback", _BI), ("Tone", _BI), ("Comb 1", _AMP),
         ("Comb 2", _AMP), ("Comb 3", _AMP), ("Pan 2", _BI), ("Pan 3", _BI),
         ("Mix", _PCT)],                                                           # Combulator
    22: [("Mode", ("menu", 0, 4)), ("Quality", ("menu", 0, 4)), ("Position", _PCT),
         ("Size", _BI), ("Pitch", (-48.0, 48.0)), ("Density", _BI), ("Texture", _BI),
         ("Spread", _PCT), ("Freeze", _PCT), ("Feedback", _PCT), ("Reverb", _PCT),
         ("Mix", _PCT)],                                                           # Nimbus
    23: [("Drive", _PCT), ("Saturation", _PCT), ("Bias", _PCT), ("Tone", _BI),
         ("Speed", (1.0, 50.0)), ("Gap", (1.0, 50.0)), ("Spacing", (0.1, 20.0)),
         ("Thickness", (0.1, 50.0)), ("Depth", _PCT), ("Amount", _PCT), ("Variance", _PCT),
         ("Mix", _PCT)],                                                           # Tape
    24: [("Threshold", (-96.0, 0.0)), ("Speed", _PCT), ("High Cut", _HZ), ("Low Cut", _HZ),
         ("Pitch", (-60.0, 60.0)), ("Ring Modulation", _PCT), ("Width", _BI), ("Mix", _PCT)]
        + [(None, None)] * 4,                                                      # Treemonster
    25: [("Pre Low Cut", _HZ), ("Pre High Cut", _HZ), ("Shape", None), ("Bias", _BI),
         ("Drive", _DB24), ("Post Low Cut", _HZ), ("Post High Cut", _HZ), ("Gain", _DB24),
         ("Mix", _PCT), (None, None), (None, None), (None, None)],                 # Waveshaper
    26: [("Matrix", ("menu", 0, 3)), ("Mid Filter Low Cut", _HZ), ("Mid Filter Gain", _DB24),
         ("Mid Filter Frequency", _HZ), ("Mid Filter High Cut", _HZ), ("Side Filter Low Cut", _HZ),
         ("Side Filter Gain", _DB24), ("Side Filter Frequency", _HZ), ("Side Filter High Cut", _HZ),
         ("Mid Gain", (-48.0, 12.0)), ("Side Gain", (-48.0, 12.0)),
         ("Balance", _BI)],                                                        # Mid-Side Tool
    27: [("Size", _PCT), ("Decay", _PCT), ("Reflections", _PCT), ("HF Damping", _PCT),
         ("Spin", _PCT), ("Chaos", _PCT), ("Knock", _PCT), ("Mix", _PCT)]
        + [(None, None)] * 4,                                                      # Spring Reverb
    28: [("Input Gain", _DB24), ("Bass Boost", _DB24), ("Bass Distort", _PCT),
         ("Bias Filter", ("menu", 0, 2)), ("Saturation Type", ("menu", 0, 3)),
         ("Saturation", _PCT), ("Noise Sensitivity", _PCT), ("Noise Gain", _DB24),
         ("Dull", _PCT), ("Output Gain", _DB24), ("Mix", _PCT), (None, None)],     # Bonsai
    29: [("Audio Input Channel", _BI), ("Audio Input Pan", _BI),
         ("Audio Input Level", (-96.0, 0.0)), ("Effect Input Channel", _BI),
         ("Effect Input Pan", _BI), ("Effect Input Level", (-96.0, 0.0)),
         ("Scene Input Channel", _BI), ("Scene Input Pan", _BI),
         ("Scene Input Level", (-96.0, 0.0)), ("Width", _BI), ("Mix", _PCT),
         (None, None)],                                                            # Audio In
    30: [("Time", (math.log2(0.02), 3.0)), ("Playrate", (-5.0, 5.0)), ("Feedback", _PCT),
         ("Cutoff", _HZ), ("Resonance", _PCT), ("Warp Rate", (-3.0, 4.0)),
         ("Warp Width", _PCT), ("Pitch Depth", _PCT), ("Filter Depth", _PCT),
         ("Low Cut", _HZ), ("Mix", _PCT), (None, None)],                           # Floaty Delay
    31: [("Pre-Delay", (0.0, 1.0)), ("Size", (0.5, 2.0)), ("Start", (0.0, 0.9)),
         ("Reverse From", (0.0, 0.5)), ("Tilt Center", None), ("Tilt Slope", (-12.0, 12.0)),
         ("Low Cut", _HZ), ("High Cut", _HZ), ("Mix", _PCT), (None, None), (None, None),
         (None, None)],                                                            # Convolution
}

# Every type's names were read off Surge in Live (its labels' last part, with the section
# where two controls share it); Airwindows' controls follow the chosen Airwindows effect.
VERIFIED = set(range(1, 32))                # every type, Surge XT 1.4.0

# The values Surge starts each FX type with (unused controls as 0).
FX_DEFAULTS: dict[int, list[dict[str, str]]] = {
    1: [  # Delay
        {"type": "2", "value": "-2.00000000000000", "deform_type": "0"},
        {"type": "2", "value": "-2.00000000000000", "deactivated": "0", "deform_type": "0"},
        {"type": "2", "value": "0.50000000000000", "extend_range": "0", "deform_type": "1"},
        {"type": "2", "value": "0.00000000000000", "extend_range": "0"},
        {"type": "2", "value": "-24.00000000000000", "deactivated": "0"},
        {"type": "2", "value": "30.00000000000000", "deactivated": "0"},
        {"type": "2", "value": "-2.00000000000000"},
        {"type": "2", "value": "0.00000000000000", "extend_range": "0"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "0", "value": "0"},
        {"type": "2", "value": "0.50000000000000"},
        {"type": "2", "value": "0.00000000000000"},
    ],
    2: [  # Reverb 1
        {"type": "2", "value": "-4.00000000000000"},
        {"type": "0", "value": "0"},
        {"type": "2", "value": "0.50000000000000"},
        {"type": "2", "value": "1.00000000000000"},
        {"type": "2", "value": "0.20000000298023"},
        {"type": "2", "value": "-24.00000000000000", "deactivated": "0"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "70.00000000000000", "deactivated": "0"},
        {"type": "2", "value": "0.50000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "0", "value": "0"},
    ],
    3: [  # Phaser
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000", "deactivated": "1"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.50000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "0", "value": "4"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "0", "value": "0"},
        {"type": "2", "value": "0.00000000000000", "deactivated": "1"},
    ],
    4: [  # Rotary
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000", "deactivated": "0"},
        {"type": "0", "value": "0"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.33000001311302"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
    ],
    5: [  # Distortion
        {"type": "2", "value": "0.00000000000000", "extend_range": "0"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "2.00000000000000"},
        {"type": "2", "value": "0.00000000000000", "deactivated": "0"},
        {"type": "2", "value": "0.00000000000000", "extend_range": "0"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000", "extend_range": "0"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "2.00000000000000"},
        {"type": "2", "value": "0.00000000000000", "deactivated": "0"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "0", "value": "0"},
    ],
    6: [  # EQ
        {"type": "2", "value": "0.00000000000000", "deactivated": "0"},
        {"type": "2", "value": "-30.00000000000000"},
        {"type": "2", "value": "2.00000000000000"},
        {"type": "2", "value": "0.00000000000000", "deactivated": "0"},
        {"type": "2", "value": "6.00000000000000"},
        {"type": "2", "value": "2.00000000000000"},
        {"type": "2", "value": "0.00000000000000", "deactivated": "0"},
        {"type": "2", "value": "54.00000000000000"},
        {"type": "2", "value": "2.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "1.00000000000000"},
        {"type": "0", "value": "0"},
    ],
    7: [  # Freq Shift
        {"type": "2", "value": "0.00000000000000", "extend_range": "0"},
        {"type": "2", "value": "1.00000000000000"},
        {"type": "2", "value": "-8.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "1.00000000000000"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
    ],
    8: [  # Conditioner
        {"type": "2", "value": "0.00000000000000", "deactivated": "0"},
        {"type": "2", "value": "1.00000000000000", "deactivated": "0"},
        {"type": "2", "value": "-1.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "1.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "-60.00000000000000", "deactivated": "1"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
    ],
    9: [  # Chorus
        {"type": "2", "value": "-6.00000000000000"},
        {"type": "2", "value": "-2.00000000000000"},
        {"type": "2", "value": "0.30000001192093"},
        {"type": "2", "value": "0.50000000000000"},
        {"type": "2", "value": "-36.00000000000000", "deactivated": "0"},
        {"type": "2", "value": "36.00000000000000", "deactivated": "0"},
        {"type": "2", "value": "1.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
    ],
    10: [  # Vocoder
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "-96.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "20"},
        {"type": "2", "value": "-15.47408008575439"},
        {"type": "2", "value": "49.09577941894531"},
        {"type": "0", "value": "0"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "1.00000000000000"},
    ],
    11: [  # Reverb 2
        {"type": "2", "value": "-4.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.75000000000000"},
        {"type": "2", "value": "1.00000000000000"},
        {"type": "2", "value": "1.00000000000000"},
        {"type": "2", "value": "0.50000000000000"},
        {"type": "2", "value": "0.20000000298023"},
        {"type": "2", "value": "0.20000000298023"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.33000001311302"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
    ],
    12: [  # Flanger
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "2", "value": "-2.00000000000000"},
        {"type": "2", "value": "1.00000000000000"},
        {"type": "2", "value": "4.00000000000000"},
        {"type": "2", "value": "60.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.10000000149012"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.80000001192093"},
        {"type": "0", "value": "0"},
    ],
    13: [  # Ring Mod
        {"type": "0", "value": "0"},
        {"type": "2", "value": "60.00000000000000"},
        {"type": "2", "value": "0.20000000298023", "extend_range": "0"},
        {"type": "0", "value": "1"},
        {"type": "2", "value": "0.30000001192093"},
        {"type": "2", "value": "0.69999998807907"},
        {"type": "2", "value": "-60.00000000000000", "deactivated": "0"},
        {"type": "2", "value": "70.00000000000000", "deactivated": "0"},
        {"type": "2", "value": "1.00000000000000"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
    ],
    14: [  # Airwindows
        {"type": "0", "value": "0", "deactivated": "0"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
    ],
    15: [  # Neuron
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.50000000000000"},
        {"type": "2", "value": "0.50000000000000"},
        {"type": "2", "value": "1.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "70.00000000000000"},
        {"type": "2", "value": "0.50000000000000"},
        {"type": "0", "value": "0"},
        {"type": "2", "value": "-2.00000000000000", "deactivated": "0"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
    ],
    16: [  # Graphic EQ
        {"type": "2", "value": "0.00000000000000", "deactivated": "0"},
        {"type": "2", "value": "0.00000000000000", "deactivated": "0"},
        {"type": "2", "value": "0.00000000000000", "deactivated": "0"},
        {"type": "2", "value": "0.00000000000000", "deactivated": "0"},
        {"type": "2", "value": "0.00000000000000", "deactivated": "0"},
        {"type": "2", "value": "0.00000000000000", "deactivated": "0"},
        {"type": "2", "value": "0.00000000000000", "deactivated": "0"},
        {"type": "2", "value": "0.00000000000000", "deactivated": "0"},
        {"type": "2", "value": "0.00000000000000", "deactivated": "0"},
        {"type": "2", "value": "0.00000000000000", "deactivated": "0"},
        {"type": "2", "value": "0.00000000000000", "deactivated": "0"},
        {"type": "2", "value": "0.00000000000000"},
    ],
    17: [  # Resonator
        {"type": "2", "value": "-18.63050079345703", "extend_range": "0"},
        {"type": "2", "value": "0.75000000000000", "extend_range": "0"},
        {"type": "2", "value": "0.79370051622391"},
        {"type": "2", "value": "8.03821563720703", "extend_range": "0"},
        {"type": "2", "value": "0.75000000000000", "extend_range": "0"},
        {"type": "2", "value": "0.79370051622391"},
        {"type": "2", "value": "35.90134811401367", "extend_range": "0"},
        {"type": "2", "value": "0.75000000000000", "extend_range": "0"},
        {"type": "2", "value": "0.79370051622391"},
        {"type": "0", "value": "1"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "1.00000000000000"},
    ],
    18: [  # CHOW
        {"type": "2", "value": "-24.00000000000000"},
        {"type": "2", "value": "10.00000000000000"},
        {"type": "0", "value": "1"},
        {"type": "2", "value": "1.00000000000000"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
    ],
    19: [  # Exciter
        {"type": "2", "value": "0.50000000000000"},
        {"type": "2", "value": "0.50000000000000"},
        {"type": "2", "value": "0.50000000000000"},
        {"type": "2", "value": "0.50000000000000"},
        {"type": "2", "value": "0.50000000000000"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
    ],
    20: [  # Ensemble
        {"type": "2", "value": "45.23264312744141"},
        {"type": "2", "value": "-2.47393107414246"},
        {"type": "2", "value": "1.00000000000000"},
        {"type": "2", "value": "2.46466827392578"},
        {"type": "2", "value": "0.60000002384186"},
        {"type": "0", "value": "2"},
        {"type": "2", "value": "40.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "1.00000000000000"},
        {"type": "2", "value": "1.00000000000000"},
        {"type": "2", "value": "45.23264312744141", "deactivated": "0"},
    ],
    21: [  # Combulator
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "-9.00000000000000", "extend_range": "0"},
        {"type": "2", "value": "0.25000000000000", "extend_range": "0"},
        {"type": "2", "value": "-0.25000000000000", "extend_range": "0"},
        {"type": "2", "value": "0.75000000000000"},
        {"type": "2", "value": "0.00000000000000", "deactivated": "0"},
        {"type": "2", "value": "0.79370051622391"},
        {"type": "2", "value": "0.79370051622391"},
        {"type": "2", "value": "0.79370051622391"},
        {"type": "2", "value": "0.25000000000000"},
        {"type": "2", "value": "-0.25000000000000"},
        {"type": "2", "value": "1.00000000000000"},
    ],
    22: [  # Nimbus
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.50000000000000"},
    ],
    23: [  # Tape
        {"type": "2", "value": "0.85000002384186", "deactivated": "0", "deform_type": "1"},
        {"type": "2", "value": "0.50000000000000"},
        {"type": "2", "value": "0.50000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "30.00000000000000", "deactivated": "0"},
        {"type": "2", "value": "1.00000000000000"},
        {"type": "2", "value": "0.10000000149012"},
        {"type": "2", "value": "0.10000000149012"},
        {"type": "2", "value": "0.00000000000000", "deactivated": "0"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "1.00000000000000"},
    ],
    24: [  # Treemonster
        {"type": "2", "value": "-24.00000000000000"},
        {"type": "2", "value": "0.50000000000000"},
        {"type": "2", "value": "0.00000000000000", "deactivated": "0"},
        {"type": "2", "value": "0.00000000000000", "deactivated": "0"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.50000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "1.00000000000000"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
    ],
    25: [  # Waveshaper
        {"type": "2", "value": "-60.00000000000000", "deactivated": "1"},
        {"type": "2", "value": "70.00000000000000", "deactivated": "1"},
        {"type": "0", "value": "0", "deactivated": "0"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000", "extend_range": "0"},
        {"type": "2", "value": "-60.00000000000000", "deactivated": "0"},
        {"type": "2", "value": "70.00000000000000", "deactivated": "0"},
        {"type": "2", "value": "0.00000000000000", "extend_range": "0"},
        {"type": "2", "value": "1.00000000000000"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
    ],
    26: [  # Mid-Side Tool
        {"type": "0", "value": "0"},
        {"type": "2", "value": "-60.00000000000000", "deactivated": "1"},
        {"type": "2", "value": "0.00000000000000", "deactivated": "1"},
        {"type": "2", "value": "-6.63048982620239"},
        {"type": "2", "value": "70.00000000000000", "deactivated": "1"},
        {"type": "2", "value": "-60.00000000000000", "deactivated": "1"},
        {"type": "2", "value": "0.00000000000000", "deactivated": "1"},
        {"type": "2", "value": "50.21310043334961"},
        {"type": "2", "value": "70.00000000000000", "deactivated": "1"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
    ],
    27: [  # Spring Reverb
        {"type": "2", "value": "0.50000000000000"},
        {"type": "2", "value": "0.50000000000000"},
        {"type": "2", "value": "1.00000000000000"},
        {"type": "2", "value": "0.50000000000000"},
        {"type": "2", "value": "0.50000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.50000000000000"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
    ],
    28: [  # Bonsai
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000", "extend_range": "0", "deform_type": "0"},
        {"type": "2", "value": "0.25000000000000"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "2", "value": "0.25000000000000"},
        {"type": "2", "value": "0.25000000000000"},
        {"type": "2", "value": "-12.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "1.00000000000000"},
        {"type": "0", "value": "0"},
    ],
    29: [  # Audio In
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "-96.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "-96.00000000000000"},
        {"type": "2", "value": "1.00000000000000"},
        {"type": "2", "value": "1.00000000000000"},
        {"type": "0", "value": "0"},
    ],
    30: [  # Floaty Delay
        {"type": "2", "value": "-1.73696994781494"},
        {"type": "2", "value": "1.00000000000000"},
        {"type": "2", "value": "0.50000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.50000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "-60.00000000000000"},
        {"type": "2", "value": "0.30000001192093"},
        {"type": "0", "value": "0"},
    ],
    31: [  # Convolution
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "1.00000000000000"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "0.00000000000000", "deactivated": "1"},
        {"type": "2", "value": "0.50000000000000", "deactivated": "1"},
        {"type": "2", "value": "0.00000000000000"},
        {"type": "2", "value": "-60.00000000000000", "deactivated": "1"},
        {"type": "2", "value": "70.00000000000000", "deactivated": "1"},
        {"type": "2", "value": "1.00000000000000"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
        {"type": "0", "value": "0"},
    ],
}


# What a control's value means, for values given with a unit ("2 s", "6 kHz", "30 %"):
# found from its range, with the exceptions below.
_TIMES = {_TIME, (-4.0, 6.0), (-8.0, 1.0), (math.log2(0.02), 3.0)}
_RATES = {_RATE, (math.log2(0.01), math.log2(20)), (-3.0, 4.0)}
_FREQS = {_HZ, (_st(60), _st(300)), (_st(300), _st(1500)), (_st(1500), _st(7500)),
          (_st(0.5), 70.0), (-36.0, 36.0), (0.0, 60.0)}
_PERCENTS = {_PCT, _BI, (0.0, 2.0), (0.0, 0.9), (0.0, 0.5), (0.5, 2.0)}
_DECIBELS = {_DB24, _DB48, (-12.0, 12.0), (-48.0, 0.0), (-96.0, 0.0), (-48.0, 12.0)}
_UNIT_EXCEPTIONS: dict[tuple[int, int], str | None] = {
    (1, 7): "cents", (13, 2): "cents", (13, 1): "note",
    (17, 2): "amp", (17, 5): "amp", (17, 8): "amp",
    (21, 6): "amp", (21, 7): "amp", (21, 8): "amp",
    (19, 2): None, (19, 3): None, (27, 1): None,          # ms / s shown on a curve
    (31, 0): None, (31, 4): None,                         # seconds 0..1; tilt center
}


def fx_unit(fx_type: int, k: int) -> str | None:
    """time | rate | hz | note | percent | cents | db | amp, or None (numbers only)."""
    if (fx_type, k) in _UNIT_EXCEPTIONS:
        return _UNIT_EXCEPTIONS[(fx_type, k)]
    entries = FX_PARAMS.get(fx_type) or []
    spec = entries[k][1] if k < len(entries) else None
    if not isinstance(spec, tuple) or spec[0] == "menu":
        return None
    for kind, ranges in (("time", _TIMES), ("rate", _RATES), ("hz", _FREQS),
                         ("percent", _PERCENTS), ("db", _DECIBELS)):
        if spec in ranges:
            return kind
    return None
