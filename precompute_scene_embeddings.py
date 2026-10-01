"""
Genera scene_embeddings.npz: los embeddings de texto de CLIP para el vocabulario
de escenas. Se corre UNA sola vez (el resultado es un archivo de ~200 KB).

Después el pipeline solo necesita el encoder visual, que ya corre igual.

    python precompute_scene_embeddings.py
    python precompute_scene_embeddings.py --model_dir /root/IMGorgni/models
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from utils_analisis import SCENE_VOCAB, SCENE_LABELS

# Cada modelo visual necesita SU encoder de texto: los embeddings solo son
# comparables dentro de la misma familia y dimensión.
# (repo, longitud de contexto, id del token de relleno)
#
# El pad_id es crítico y cambia por familia. Estos ONNX no reciben
# attention_mask, así que el relleno entra como texto real:
#   - CLIP    agrupa por la posición del EOS -> el relleno no lo afecta.
#   - SigLIP  agrupa por el ÚLTIMO token (pool_type "last", sin máscara causal).
#     Si el relleno no es el que vio en entrenamiento, se lee el embedding de
#     una posición fuera de distribución: no degrada, directamente delira.
#       SigLIP v1 (tokenizer T5)    -> </s>  = 1
#       SigLIP 2  (tokenizer Gemma) -> <pad> = 0   <-- ojo, al revés que v1
# Verificado sobre una foto de un Ferrari contra 6 etiquetas:
#   pad=0 -> "a city skyline" (-0.011)   |   pad=1 -> "a car" (+0.053)
# Estos valores son solo el respaldo: resolver_pad() lee el real del modelo.
MODELOS = {
    "b32":      ("immich-app/ViT-B-32__openai", 77, 0),
    "siglip":   ("immich-app/ViT-B-16-SigLIP__webli", 64, 1),
    "so400m":   ("immich-app/ViT-SO400M-14-SigLIP-384__webli", 64, 1),
    "siglip2":  ("immich-app/ViT-SO400M-16-SigLIP2-384__webli", 64, 0),
}
REPO_ID = MODELOS["b32"][0]
CONTEXT_LENGTH = 77


def download_textual(model_dir: Path, repo: str) -> tuple[Path, Path]:
    from huggingface_hub import hf_hub_download
    model = hf_hub_download(repo_id=repo, filename="textual/model.onnx",
                            local_dir=str(model_dir))
    tok = hf_hub_download(repo_id=repo, filename="textual/tokenizer.json",
                          local_dir=str(model_dir))
    try:   # opcional: de acá sale el token de relleno correcto
        hf_hub_download(repo_id=repo, filename="textual/tokenizer_config.json",
                        local_dir=str(model_dir))
    except Exception:
        pass
    return Path(model), Path(tok)


def resolver_pad(tokenizer, tokenizer_path: Path, por_defecto: int) -> tuple[int, str]:
    """
    Token de relleno leído del propio modelo, no hardcodeado.

    Es crítico y cambia entre familias: SigLIP v1 (T5) rellena con </s>=1,
    SigLIP 2 (Gemma) con <pad>=0, CLIP con 0. Equivocarse no da error: da
    clasificaciones absurdas. Se resuelve desde tokenizer_config.json, que es
    la fuente de verdad que publica el propio repo del modelo.
    """
    cfg_path = tokenizer_path.parent / "tokenizer_config.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            tok = cfg.get("pad_token")
            if isinstance(tok, dict):
                tok = tok.get("content")
            if tok:
                pid = tokenizer.token_to_id(tok)
                # CLIP declara <|endoftext|> como pad pero se entrenó con 0;
                # como agrupa por la posición del EOS, el relleno da igual.
                if pid is not None and "clip" not in str(
                        cfg.get("tokenizer_class", "")).lower():
                    return pid, f"{tok!r} (de tokenizer_config.json)"
        except Exception:
            pass
    return por_defecto, "valor por defecto de la tabla"


def canonicalizar(texto: str) -> str:
    """
    Normaliza como el preprocesado oficial de SigLIP: minúsculas, sin
    puntuación, espacios colapsados. Aplicarlo de más no rompe CLIP.
    """
    import re
    import string
    sin_punt = texto.translate(str.maketrans("", "", string.punctuation))
    return re.sub(r"\s+", " ", sin_punt).strip().lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model_dir", default="models")
    ap.add_argument("--out", default="scene_embeddings.npz")
    ap.add_argument("--modelo", default="b32", choices=list(MODELOS),
                    help="b32 (rapido) | siglip | so400m (maxima calidad)")
    args = ap.parse_args()

    model_dir = Path(args.model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)

    repo, ctx, pad_id = MODELOS[args.modelo]
    print(f"Encoder de texto: {repo}  (contexto {ctx}, pad_id={pad_id})")
    model_path, tokenizer_path = download_textual(model_dir, repo)

    from tokenizers import Tokenizer
    import onnxruntime as ort

    tokenizer = Tokenizer.from_file(str(tokenizer_path))
    pad_id, origen = resolver_pad(tokenizer, tokenizer_path, pad_id)
    print(f"   relleno: id={pad_id}  ({origen})")
    tokenizer.enable_padding(length=ctx, pad_id=pad_id)
    tokenizer.enable_truncation(max_length=ctx)

    prompts = [canonicalizar(f"a photo of {v}") for v in SCENE_VOCAB]
    encoded = tokenizer.encode_batch(prompts)

    sess = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    inp = sess.get_inputs()[0]
    input_name = inp.name
    # El modelo textual de immich espera batch fijo de 1; su dtype puede ser int32 o int64
    dtype = np.int64 if "int64" in str(inp.type) else np.int32
    batch_dim = inp.shape[0] if inp.shape else None

    print(f"Codificando {len(prompts)} descripciones de escena...")
    ids = np.array([e.ids for e in encoded], dtype=dtype)
    if isinstance(batch_dim, int) and batch_dim == 1:
        vecs = [sess.run(None, {input_name: ids[i:i + 1]})[0][0] for i in range(len(ids))]
        embeddings = np.asarray(vecs, dtype=np.float32)
    else:
        embeddings = np.asarray(sess.run(None, {input_name: ids})[0], dtype=np.float32)

    # NO se centra: el centrado se validó contra similitud texto-texto, pero la
    # tarea real es imagen-texto. La brecha entre modalidades es común a todas
    # las etiquetas y se cancela en el ranking; restar la media de los textos al
    # embedding de imagen rompe la alineación (verificado: SO400M pasaba a
    # clasificar todo como "fireworks"). La selección usa z-score en runtime.
    embeddings /= np.maximum(np.linalg.norm(embeddings, axis=1, keepdims=True), 1e-8)
    mean = embeddings.mean(axis=0)
    centered = embeddings

    np.savez_compressed(args.out, embeddings=centered, mean=mean, centrado=False,
                        labels=np.array(SCENE_LABELS, dtype=object))
    print(f"✅ {args.out}: {centered.shape[0]} escenas × {centered.shape[1]} dims")

    # Chequeo de sanidad sobre los embeddings YA centrados
    def sim(a: str, b: str) -> float:
        i, j = SCENE_LABELS.index(a), SCENE_LABELS.index(b)
        return float(centered[i] @ centered[j])

    # La similitud texto-texto es alta en todos los modelos (anisotropía) y no
    # predice la calidad imagen-texto, así que solo se verifica lo estructural.
    assert np.isfinite(centered).all(), "Hay NaN/inf en los embeddings"
    normas = np.linalg.norm(centered, axis=1)
    assert np.allclose(normas, 1.0, atol=1e-3), f"Sin normalizar: {normas[:3]}"
    print(f"   sanity: normas=1.0 OK | beach~surfing={sim('beach','surfing'):.3f}")


if __name__ == "__main__":
    main()
