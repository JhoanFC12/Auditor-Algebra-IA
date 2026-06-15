---
base_model: Qwen/Qwen2.5-0.5B-Instruct
library_name: transformers
model_name: math-ocr-normalizer-qwen2.5-0.5b-lora-v1
tags:
- generated_from_trainer
- trl
- sft
- trackio:https://Jhoan12-trackio.hf.space?project=auditor-ia-normalizer&runs=normalizer-v1-300-qwen25-05b-lora&sidebar=collapsed
- hf_jobs
licence: license
---

# Model Card for math-ocr-normalizer-qwen2.5-0.5b-lora-v1

This model is a fine-tuned version of [Qwen/Qwen2.5-0.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct).
It has been trained using [TRL](https://github.com/huggingface/trl).

## Quick start

```python
from transformers import pipeline

question = "If you had a time machine, but could only go to the past or the future once and never return, which would you choose and why?"
generator = pipeline("text-generation", model="Jhoan12/math-ocr-normalizer-qwen2.5-0.5b-lora-v1", device="cuda")
output = generator([{"role": "user", "content": question}], max_new_tokens=128, return_full_text=False)[0]
print(output["generated_text"])
```

## Training procedure

 
[<img src="https://raw.githubusercontent.com/gradio-app/trackio/refs/heads/main/trackio/assets/badge.png" alt="Visualize in Trackio" title="Visualize in Trackio" width="150" height="24"/>](https://Jhoan12-trackio.hf.space?project=auditor-ia-normalizer&runs=normalizer-v1-300-qwen25-05b-lora&sidebar=collapsed)


This model was trained with SFT.

### Framework versions

- TRL: 1.6.0
- Transformers: 4.57.6
- Pytorch: 2.5.1+cu124
- Datasets: 5.0.0
- Tokenizers: 0.22.2

## Citations



Cite TRL as:
    
```bibtex
@software{vonwerra2020trl,
  title   = {{TRL: Transformers Reinforcement Learning}},
  author  = {von Werra, Leandro and Belkada, Younes and Tunstall, Lewis and Beeching, Edward and Thrush, Tristan and Lambert, Nathan and Huang, Shengyi and Rasul, Kashif and Gallouédec, Quentin},
  license = {Apache-2.0},
  url     = {https://github.com/huggingface/trl},
  year    = {2020}
}
```