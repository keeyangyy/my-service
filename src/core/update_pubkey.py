"""更新清单签名公钥（Ed25519，hex）。

轮换密钥：用 `tools/gen_update_key.py` 生成新对，更新本文件并**同时发版**——
旧客户端只认旧公钥，新私钥签的清单在旧客户端会被拒绝。
"""
PUBLIC_KEY_HEX = "0361bfad43d1808ac50e8b3e829f218272358a13b133b9619dcd87c55d3e13e8"
