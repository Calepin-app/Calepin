"""Client minimal pour le serveur local de LM Studio (API compatible OpenAI). Rien ne sort de la machine."""
import json
import subprocess
import time
import urllib.error
import urllib.request

from config import LLM_MODEL, LLM_URL, LMS_BIN, read_conf
from i18n import tr


class PipelineError(Exception):
    """Erreur dont le message ne contient jamais de données bancaires (affichable tel quel)."""


def _post(payload, timeout):
    req = urllib.request.Request(
        LLM_URL, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _get(path):
    with urllib.request.urlopen(LLM_URL.rsplit("/v1/", 1)[0] + path, timeout=5) as r:
        return json.loads(r.read())


def models():
    """Modèles de conversation installés : [{"id", "loaded"}], sans les modèles d'embedding."""
    try:  # API propre à LM Studio : type et état de chargement de chaque modèle installé
        return [{"id": m["id"], "loaded": m.get("state") == "loaded"}
                for m in _get("/api/v0/models")["data"] if m.get("type") in ("llm", "vlm")]
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
        pass
    try:  # autre serveur compatible OpenAI
        return [{"id": m["id"], "loaded": False} for m in _get("/v1/models")["data"] if "embed" not in m["id"]]
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
        raise PipelineError(tr("llm.unreachable"))


def chosen_model():
    """Le modèle imposé (BANQUE_LLM_MODEL) ou choisi dans la page ; None s'il n'y en a pas."""
    return LLM_MODEL or read_conf().get("modele")


def model():
    """Modèle à utiliser : le modèle choisi ; à défaut le seul modèle chargé, ou le seul installé."""
    if chosen_model():
        return chosen_model()
    ms = models()
    loaded = [m["id"] for m in ms if m["loaded"]]
    if len(loaded) == 1:
        return loaded[0]
    if len(ms) == 1:
        return ms[0]["id"]
    raise PipelineError(tr("llm.choose_model", n=len(ms)) if ms else tr("llm.no_model"))


def ensure_server():
    for attempt in range(2):
        try:
            _get("/v1/models")
            return
        except (urllib.error.URLError, OSError):
            if attempt == 0 and LMS_BIN.exists():
                subprocess.run([str(LMS_BIN), "server", "start"], capture_output=True, timeout=60)
                time.sleep(2)
    raise PipelineError(tr("llm.unreachable"))


def chat(system, user, schema=None, temperature=0.0, timeout=600, max_tokens=2000):
    """Renvoie le texte, ou l'objet JSON si `schema` (JSON Schema) est fourni."""
    name = model()
    payload = {
        "model": name,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    if schema:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "reponse", "strict": True, "schema": schema},
        }
    try:
        choice = _post(payload, timeout)["choices"][0]
        content = choice["message"]["content"]
    except urllib.error.HTTPError as e:
        raise PipelineError(tr("llm.http", code=e.code, model=name))
    except (urllib.error.URLError, TimeoutError, OSError):
        raise PipelineError(tr("llm.timeout"))
    if choice.get("finish_reason") == "length":
        raise PipelineError(tr("llm.truncated"))
    if not schema:
        return content.strip()
    try:
        start, end = content.index("{"), content.rindex("}") + 1
        return json.loads(content[start:end])
    except ValueError:
        raise PipelineError(tr("llm.bad_json"))
