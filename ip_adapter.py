import torch
import os
from .image_preprocessor import pad_to_square, face_crop

CURRENT_DIR = os.path.dirname(os.path.realpath(__file__))

SD_V12_CHANNELS = [320] * 4 + [640] * 4 + [1280] * 10 + [640] * 6 + [320] * 6 + [1280] * 2
SD_XL_CHANNELS = [640] * 8 + [1280] * 40 + [1280] * 60 + [640] * 12 + [1280] * 20

def get_file_list(path):
    return [file for file in os.listdir(path) if file != "put_models_here.txt"]

def set_model_patch_replace(model, patch, block_name, number, index):
    name = "attn2"
    to = model.model_options["transformer_options"]
    if "patches_replace" not in to:
        to["patches_replace"] = {}
    if name not in to["patches_replace"]:
        to["patches_replace"][name] = {}
    to["patches_replace"][name][(block_name, number, index)] = patch

class ImageProjModel(torch.nn.Module):
    """Projection Model"""
    def __init__(self, cross_attention_dim=1024, clip_embeddings_dim=1024, clip_extra_context_tokens=4):
        super().__init__()
        
        self.cross_attention_dim = cross_attention_dim
        self.clip_extra_context_tokens = clip_extra_context_tokens
        self.proj = torch.nn.Linear(clip_embeddings_dim, self.clip_extra_context_tokens * cross_attention_dim)
        self.norm = torch.nn.LayerNorm(cross_attention_dim)
        
    def forward(self, image_embeds):
        embeds = image_embeds
        clip_extra_context_tokens = self.proj(embeds).reshape(-1, self.clip_extra_context_tokens, self.cross_attention_dim)
        clip_extra_context_tokens = self.norm(clip_extra_context_tokens)
        return clip_extra_context_tokens
    
class To_KV(torch.nn.Module):
    def __init__(self, cross_attention_dim):
        super().__init__()
        channels = SD_XL_CHANNELS if cross_attention_dim == 2048 else SD_V12_CHANNELS
        self.to_kvs = torch.nn.ModuleList([torch.nn.Linear(cross_attention_dim, channel, bias=False) for channel in channels])
        
    def load_state_dict(self, state_dict):
        for i, key in enumerate(state_dict.keys()):
            self.to_kvs[i].weight.data = state_dict[key]
    
class IPAdapterModel:
    def __init__(self, ip_ckpt, clip_embeddings_dim):
        super().__init__()
        self.device = "cuda"
        state_dict = torch.load(ip_ckpt, map_location="cpu")
        self.cross_attention_dim = state_dict["ip_adapter"]["1.to_k_ip.weight"].shape[1]
        self.clip_extra_context_tokens = state_dict["image_proj"]["proj.weight"].shape[0] // self.cross_attention_dim
        self.image_proj_model = ImageProjModel(
            cross_attention_dim=self.cross_attention_dim,
            clip_embeddings_dim=clip_embeddings_dim,
            clip_extra_context_tokens=self.clip_extra_context_tokens
        ).to("cuda", dtype=torch.float16)
        
        self.load_ip_adapter(state_dict)

    def load_ip_adapter(self, state_dict):
        self.image_proj_model.load_state_dict(state_dict["image_proj"])
        self.ip_layers = To_KV(self.cross_attention_dim)
        self.ip_layers.load_state_dict(state_dict["ip_adapter"])
        self.ip_layers.to("cuda")
        
    @torch.inference_mode()
    def get_image_embeds(self, clip_image_embeds):
        image_prompt_embeds = self.image_proj_model(clip_image_embeds)
        uncond_image_prompt_embeds = self.image_proj_model(torch.zeros_like(clip_image_embeds))
        return image_prompt_embeds, uncond_image_prompt_embeds

class IPAdapter:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "model": ("MODEL", ),
                "clip_vision_output": ("CLIP_VISION_OUTPUT", ),
                "weight": ("FLOAT", {
                    "default": 1, 
                    "min": -1, #Minimum value
                    "max": 3, #Maximum value
                    "step": 0.05 #Slider's step
                }),
                "model_name": (get_file_list(os.path.join(CURRENT_DIR,"models")), ),
            }
        }
    
    RETURN_TYPES = ("MODEL",)
    FUNCTION = "adapter"
    CATEGORY = "loaders"

    def adapter(self, model, clip_vision_output, weight, model_name):
        dtype = model.model.diffusion_model.dtype
        device = "cuda"
        self.weight = weight
        
        clip_vision_emb = clip_vision_output.image_embeds.to(device, dtype=torch.float16)
        clip_embeddings_dim = clip_vision_emb.shape[1]

        self.sdxl = clip_embeddings_dim == 1280 # 本当か？
        
        self.ipadapter = IPAdapterModel(
            os.path.join(CURRENT_DIR, os.path.join(CURRENT_DIR, "models", model_name)),
            clip_embeddings_dim = clip_embeddings_dim
        )
        self.ipadapter.ip_layers.to(device, dtype=dtype)

        self.image_emb, self.uncond_image_emb = self.ipadapter.get_image_embeds(clip_vision_emb)
        self.image_emb = self.image_emb.to(device, dtype=dtype)
        self.uncond_image_emb = self.uncond_image_emb.to(device, dtype=dtype)
        self.cond_uncond_image_emb = None
        
        new_model = model.clone()
        if not self.sdxl:
            number = 0
            for id in [1,2,4,5,7,8]:
                new_model.set_model_attn2_replace(self.patch_forward(number), "input", id)
                number += 1
            for id in [3,4,5,6,7,8,9,10,11]:
                new_model.set_model_attn2_replace(self.patch_forward(number), "output", id)
                number += 1
            new_model.set_model_attn2_replace(self.patch_forward(number), "middle", 0)
        else:
            number = 0
            for id in [4,5,7,8]:
                block_indices = range(2) if id in [4, 5] else range(10)
                for index in block_indices:
                    set_model_patch_replace(new_model, self.patch_forward(number), "input", id, index)
                    number += 1
            for id in range(6):
                block_indices = range(2) if id in [3, 4, 5] else range(10)
                for index in block_indices:
                    set_model_patch_replace(new_model, self.patch_forward(number), "output", id, index)
                    number += 1
            for index in range(10):
                set_model_patch_replace(new_model, self.patch_forward(number), "middle", 0, index)
                number += 1

        return (new_model,)
    
    def patch_forward(self, number):
        def forward(n, context_attn2, value_attn2, extra_options):
            q = n
            k = context_attn2
            v = value_attn2
            b, _, _ = q.shape

            if self.cond_uncond_image_emb is None or self.cond_uncond_image_emb.shape[0] != b:
                self.cond_uncond_image_emb = torch.cat([self.uncond_image_emb.repeat(b//2, 1, 1), self.image_emb.repeat(b//2, 1, 1)], dim=0)

            ip_k = self.ipadapter.ip_layers.to_kvs[number*2](self.cond_uncond_image_emb)
            ip_v = self.ipadapter.ip_layers.to_kvs[number*2+1](self.cond_uncond_image_emb)

            q, k, v, ip_k, ip_v = map(
                lambda t: t.view(b, -1, extra_options["n_heads"], extra_options["dim_head"]).transpose(1, 2),
                (q, k, v, ip_k, ip_v),
            )

            out = torch.nn.functional.scaled_dot_product_attention(q, k, v, attn_mask=None, dropout_p=0.0, is_causal=False)
            out = out.transpose(1, 2).reshape(b, -1, extra_options["n_heads"] * extra_options["dim_head"])

            ip_out = torch.nn.functional.scaled_dot_product_attention(q, ip_k, ip_v, attn_mask=None, dropout_p=0.0, is_causal=False)
            ip_out = ip_out.transpose(1, 2).reshape(b, -1, extra_options["n_heads"] * extra_options["dim_head"])

            out = out + ip_out * self.weight

            return out

        return forward
    
class ImageCrop:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "image": ("IMAGE", ),
                "mode": (["padding", "face_crop", "none"], ), 
            }
        }
    
    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "preprocess"
    CATEGORY = "image/preprocessors"

    def preprocess(self, image, mode):
        if mode == "padding":
            image = pad_to_square(image) 
        elif mode == "face_crop":
            image = face_crop(image)
        
        return (image,)
        
NODE_CLASS_MAPPINGS = {
    "IPAdapter": IPAdapter,
    "ImageCrop": ImageCrop,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "IPAdapter": "Load IPAdapter",
    "ImageCrop": "furusu Image Crop",
}

