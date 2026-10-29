# Max objects for Live devices

The Ableton DSP, Max for Live and maxforlive-elements packages of Cycling '74's Object & Operator
Reference (https://docs.cycling74.com/reference/, 116 objects, read 2026-10-29). Each object's page
is `https://docs.cycling74.com/reference/<name>/`.

Worth knowing for LiveBridge:

- `live.modulate~`: modulates a parameter as an offset (-1..1, `depth`) around its current value
  without taking it over (unlike `live.remote~`, which the Engine uses today).
- `live.miditool.in` / `live.miditool.out`: build a MIDI Tool (Generator / Transformation) that
  reads and writes the notes of the clip being edited.
- `abl.device.*` / `abl.dsp.*`: Ableton's own device DSP (Compressor, Limiter, Roar, Drift,
  Utility...) as Max objects, for building devices out of Live's processing.

| Object | Package | Description |
|---|---|---|
| `abl.device.autofilter~` | Ableton DSP | Classic analog filter emulation. |
| `abl.device.channeleq~` | Ableton DSP | Semi-parametric 3-band channel EQ |
| `abl.device.compressor~` | Ableton DSP | Compressor |
| `abl.device.delay~` | Ableton DSP | Stereo delay |
| `abl.device.drift~` | Ableton DSP | Drift synthesizer |
| `abl.device.drumbuss~` | Ableton DSP | Analog-style drum processor |
| `abl.device.drumsampler~` | Ableton DSP | Drum sampler |
| `abl.device.echo~` | Ableton DSP | Modulation delay |
| `abl.device.limiter~` | Ableton DSP | Limiter |
| `abl.device.redux~` | Ableton DSP | Downsampling and bit-reduction effect |
| `abl.device.reverb2~` | Ableton DSP | Feedback Delay Network Reverb |
| `abl.device.reverb~` | Ableton DSP | Plate reverb |
| `abl.device.roar~` | Ableton DSP | Three-stage saturator |
| `abl.device.spectralresonator~` | Ableton DSP | Spectral resonator |
| `abl.device.spectraltime~` | Ableton DSP | Spectral delay |
| `abl.device.stereocompressor~` | Ableton DSP | Stereo Compressor |
| `abl.device.utility~` | Ableton DSP | Swiss army knife for audio |
| `abl.dsp.alternate~` | Ableton DSP | Alternating modulator |
| `abl.dsp.basicshapes~` | Ableton DSP | Basic shape oscillator |
| `abl.dsp.bitgrunge~` | Ableton DSP | Bit-grunge oscillator |
| `abl.dsp.bubble~` | Ableton DSP | Bubble generator |
| `abl.dsp.chip~` | Ableton DSP | Square wave chiptune oscillator |
| `abl.dsp.chorus~` | Ableton DSP | Chorus |
| `abl.dsp.compander~` | Ableton DSP | Compressor/expander |
| `abl.dsp.crackle~` | Ableton DSP | Crackle sound generator |
| `abl.dsp.darkhall~` | Ableton DSP | Dark hall reverb |
| `abl.dsp.dfm~` | Ableton DSP | Digitally modeled analog lowpass filter |
| `abl.dsp.distortion~` | Ableton DSP | Guitar pedal distortion |
| `abl.dsp.djfilter~` | Ableton DSP | DJ-style filter |
| `abl.dsp.doubler~` | Ableton DSP | Doubler |
| `abl.dsp.dualbasicshapes~` | Ableton DSP | Dual basic shape oscillator |
| `abl.dsp.ensemble~` | Ableton DSP | Ensemble |
| `abl.dsp.envfollower~` | Ableton DSP | Envelope follower |
| `abl.dsp.euclid~` | Ableton DSP | Euclidean rhythm ramp generator |
| `abl.dsp.expshaper~` | Ableton DSP | Exponential shaper |
| `abl.dsp.extratone~` | Ableton DSP | Granular-esque kick drum tone oscillator |
| `abl.dsp.filterednoise~` | Ableton DSP | Filtered noise generator |
| `abl.dsp.filther~` | Ableton DSP | Distorted lowpass filter |
| `abl.dsp.flanger~` | Ableton DSP | Flanger |
| `abl.dsp.fmbass~` | Ableton DSP | Three operator FM oscillator |
| `abl.dsp.foldfm~` | Ableton DSP | Wavefolding FM oscillator |
| `abl.dsp.fuzz~` | Ableton DSP | Fuzz guitar pedal |
| `abl.dsp.harmonicfm~` | Ableton DSP | Harmonic FM oscillator |
| `abl.dsp.meldfilter~` | Ableton DSP | Meta-filter |
| `abl.dsp.meldosc~` | Ableton DSP | Meta-oscillator |
| `abl.dsp.modalresonator~` | Ableton DSP | Modal resonator |
| `abl.dsp.modulator~` | Ableton DSP | Modulation signal generator |
| `abl.dsp.noiseloop~` | Ableton DSP | Noise buffer looper |
| `abl.dsp.noisyshapes~` | Ableton DSP | Noisy dual basic shape oscillator |
| `abl.dsp.overdrive~` | Ableton DSP | Overdriven guitar pedal |
| `abl.dsp.phaser~` | Ableton DSP | Phaser |
| `abl.dsp.pitchestimator~` | Ableton DSP | Frequency detector |
| `abl.dsp.pitchshifter~` | Ableton DSP | Pitch shifter |
| `abl.dsp.prism~` | Ableton DSP | Prism reverb |
| `abl.dsp.pulsate~` | Ableton DSP | Random pulse emitter |
| `abl.dsp.quartz~` | Ableton DSP | Quartz reverb |
| `abl.dsp.rain~` | Ableton DSP | Rain sound generator |
| `abl.dsp.ramp~` | Ableton DSP | Ramp generator |
| `abl.dsp.ringmod~` | Ableton DSP | Ring modulator |
| `abl.dsp.saturator~` | Ableton DSP | Saturator |
| `abl.dsp.shepard~` | Ableton DSP | Shepard tone oscillator |
| `abl.dsp.shimmer~` | Ableton DSP | Shimmer reverb |
| `abl.dsp.simplefm~` | Ableton DSP | Two-operator FM oscillator |
| `abl.dsp.squarefifth~` | Ableton DSP | Morphing square wave oscillator |
| `abl.dsp.squaresync~` | Ableton DSP | Dual square wave oscillator |
| `abl.dsp.stereolfo~` | Ableton DSP | Stereo low-frequency oscillator |
| `abl.dsp.subosc~` | Ableton DSP | Oscillator with sub bass |
| `abl.dsp.swarm~` | Ableton DSP | Multi-oscillator swarm |
| `abl.dsp.tarp~` | Ableton DSP | Kick/bass oscillator |
| `abl.dsp.tides~` | Ableton DSP | Modulating algorithmic reverb |
| `abl.dsp.transform~` | Ableton DSP | Signal transformer |
| `abl.dsp.transientdesign~` | Ableton DSP | Transient designer |
| `abl.dsp.velvetnoise~` | Ableton DSP | A velvet noise generator |
| `abl.dsp.vibrato~` | Ableton DSP | Stereo vibrato effect |
| `abl.dsp.vowel~` | Ableton DSP | Vowel bandpass filter |
| `abl.dsp.wander~` | Ableton DSP | Wandering modulator |
| `abl.dsp.waveshaper~` | Ableton DSP | Waveshaper |
| `amxd~` | Max for Live | Host Max for Live devices |
| `chucker~` | Max for Live | Segment audio and reorder it on looped playback |
| `cverb~` | Max for Live | A simple monaural reverberator |
| `ddg.mono` | Max for Live | Monophonic Synth Controller for Virtual Synths |
| `live.adsrui` | maxforlive-elements | ADSR User Interface |
| `live.adsr~` | maxforlive-elements | ADSR envelope generator |
| `live.arrows` | Max for Live | Vectorized arrow(s) user interface object |
| `live.banks` | Max for Live | Manage Max for Live Device banks for Push controllers. |
| `live.button` | Max for Live | Flash on any message, send a bang |
| `live.colors` | Max for Live | Get the colors of the active Ableton Live Theme via a Max for Live device. |
| `live.dial` | Max for Live | Output numbers by moving a dial onscreen |
| `live.drop` | Max for Live | Define a region for dragging and dropping a file |
| `live.gain~` | Max for Live | Decibel volume slider and monitor |
| `live.grid` | Max for Live | A UI grid of steps, with constraints and directions |
| `live.line` | Max for Live | Straight line |
| `live.map` | Max for Live | Simplify the process of selecting Live interface elements for use with the Live API. |
| `live.menu` | Max for Live | Dropdown menu |
| `live.meter~` | Max for Live | Live-style visual peak level indicator |
| `live.miditool.in` | Max for Live | Retrieve notes and contextual data of MIDI clips in Live. Designed for use in a MIDI Tool Generator or MIDI Tool Transformation. |
| `live.miditool.out` | Max for Live | Set or replace notes of MIDI clips in Live. Designed for use in a MIDI Tool Generator or MIDI Tool Transformation. |
| `live.modulate~` | Max for Live | Modulate Ableton Live and Max for Live Parameters |
| `live.numbox` | Max for Live | Display and output a number |
| `live.object` | Max for Live | Perform operations on Live objects |
| `live.observer` | Max for Live | Monitor changes in Live objects |
| `live.param~` | Max for Live | Generate an MSP signal from a parameter value. |
| `live.path` | Max for Live | Navigate to objects in the Live application |
| `live.push` | Max for Live | Configuration of Push 2 and 3 |
| `live.remote~` | Max for Live | Realtime control of parameters in Ableton Live and Max for Live. |
| `live.routing` | Max for Live | Route MIDI and Audio Inputs and Outputs in Ableton Live |
| `live.scope~` | maxforlive-elements | Visualize an audio signal |
| `live.slider` | Max for Live | Output numbers by moving a slider onscreen |
| `live.step` | Max for Live | Step Sequencer UI object |
| `live.tab` | Max for Live | A user interface tab/multiple button object in the style of Ableton Live. |
| `live.text` | Max for Live | A user interface button/toggle |
| `live.thisdevice` | Max for Live | Send a bang automatically when a Max Device is loaded, report device state |
| `live.toggle` | Max for Live | Switch between off and on (0/1) |
| `mc.amxd~` | Max for Live | Host Max for Live devices (multichannel) |
| `mc.live.gain~` | Max for Live | Decibel volume slider and monitor |
| `midiselect` | Max for Live | Select and interpret raw MIDI data |
