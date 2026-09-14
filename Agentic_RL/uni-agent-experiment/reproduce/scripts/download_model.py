from huggingface_hub import snapshot_download
snapshot_download('Qwen/Qwen3-Coder-30B-A3B-Instruct', revision='b2cff646eb4bb1d68355c01b18ae02e7cf42d120', local_dir='/lab/models/Qwen3-Coder-30B-A3B-Instruct', max_workers=8)
