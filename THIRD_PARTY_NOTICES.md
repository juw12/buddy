# Third-party notices

- Natural voices use [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) (Apache License 2.0) through [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) (MIT License). The sample clips in `docs/voices/` were generated with these voices.
- Speech recognition runs with [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) (Apache License 2.0). The models are downloaded on first use from its GitHub releases, not shipped in the zip:
  - [OpenAI Whisper base.en](https://github.com/openai/whisper) (MIT License), int8 export by the sherpa-onnx project.
  - [NVIDIA Parakeet TDT 0.6B v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) (CC BY 4.0), int8 export by the sherpa-onnx project.
  - [SenseVoice-Small](https://github.com/FunAudioLLM/SenseVoice) (FunAudioLLM, FunASR model license), int8 export by the sherpa-onnx project.
  - [Moonshine](https://github.com/usefulsensors/moonshine) (Useful Sensors, MIT License), optional.
  - [Silero VAD](https://github.com/snakers4/silero-vad) (MIT License) for voice activity detection.
- Chinese script conversion (Simplified and Traditional) uses the character and phrase tables of [OpenCC](https://github.com/BYVoid/OpenCC) (Apache License 2.0), copied unchanged from `ver.1.1.9` into `keyit/opencc/` together with their licence.
- The English word list that tells a real word from a misheard one comes with [misaki](https://github.com/hexgrad/misaki) (Apache License 2.0), the library Buddy already uses to read text for the natural voices.
