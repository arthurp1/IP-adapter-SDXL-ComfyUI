# IPAdapter-ComfyUI
[IP-Adapter](https://github.com/tencent-ailab/IP-Adapter)の実験的な実装です。

# Install
1. custom_nodesにくろーん
2. `IPAdapter-ComfyUI/models`に[ip-adapterのチェックポイント](https://huggingface.co/h94/IP-Adapter/blob/main/models/ip-adapter_sd15.bin)を入れる。
3. `ComfyUI/models/clip_vision`に[clip vision model](https://huggingface.co/h94/IP-Adapter/blob/main/models/image_encoder/pytorch_model.bin)を入れる。

# Usage
わーくふろぉ貼ってます。

# CITIATION
```
@article{ye2023ip-adapter,
  title={IP-Adapter: Text Compatible Image Prompt Adapter for Text-to-Image Diffusion Models},
  author={Ye, Hu and Zhang, Jun and Liu, Sibo and Han, Xiao and Yang, Wei},
  booktitle={arXiv preprint arxiv:2308.06721},
  year={2023}
}
```