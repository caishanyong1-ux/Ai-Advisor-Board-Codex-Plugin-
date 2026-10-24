"""Local, script-free forms for user-owned API connections."""
import html
import json

from .connections import PRESETS, endpoint_url
from .core import BoardError
from . import meta, localization


def esc(value):
    return html.escape(str(value), quote=True)


def field(name, label, value="", kind="text", required=False, extra=""):
    return "<label>%s<input type='%s' name='%s' value='%s' %s %s></label>" % (esc(label), kind, name, esc(value), "required" if required else "", extra)


def hidden(token, connection=""):
    return "<input type='hidden' name='token' value='%s'><input type='hidden' name='connection' value='%s'>" % (esc(token), esc(connection))


def render(board, token, query=None, notice="", discovered=None, t=None):
    t = t or localization.for_ticket(board.store, token)
    query = query or {}
    base = "/confirm/" + token
    selected = query.get("connection", "")
    row = board.connections.get(selected) if selected else None
    preset_id = query.get("preset", "custom")
    preset = PRESETS.get(preset_id, PRESETS["custom"])
    settings = row or preset
    if row and query.get("saved") == "1":
        return (t("<h2>%s saved</h2><p>Return to Codex. Codex handles model and pricing settings.</p><p><a href='%s?connection=%s'>Edit API</a> · <a href='%s'>Add another API</a></p>")
                % (esc(row["name"]), base, esc(selected), base))
    body = t('<p>Add your API connection. Codex handles model and pricing settings.</p>')
    if notice:
        body += "<p class='notice'>" + esc(notice) + "</p>"
    if not row:
        body += "<nav>" + " · ".join("<a href='%s?preset=%s'>%s</a>" % (base, key, esc(t(p["name"]))) for key, p in PRESETS.items()) + "</nav>"
    body += "<h2>%s</h2><form method='post' action='/api/save'>" % (t('Edit API') if row else t('Add API')) + hidden(token, selected)
    body += "<input type='hidden' name='preset' value='%s'>" % esc(preset_id)
    body += field("name", t('Name'), (settings["name"] if row else t(settings["name"])), required=True, extra="maxlength='100'")
    body += field("base_url", t('API URL'), settings["base_url"], required=True, extra="maxlength='1000' placeholder='https://provider.example/v1'")
    label = t('API key (leave blank to keep the existing key)') if row and row["has_key"] else t("API key")
    body += field("api_key", label, kind="password", extra="autocomplete='new-password' maxlength='8192'")
    body += t('<p><small>The key stays on this Mac. Saving does not call paid models.</small></p><button>Save API</button></form>')
    if row:
        body += t("<h2>Manage API</h2><form method='post' action='/api/manage'>") + hidden(token, selected)
        body += "<button name='action' value='%s'>%s</button></form>" % (("disable", t('Disable')) if row["enabled"] else ("enable", t('Enable')))
        body += t("<details><summary>Remove this API</summary><form method='post' action='/api/manage'>") + hidden(token, selected)
        body += t("<label><input type='checkbox' name='delete_confirm' value='yes' required> Remove the connection and key; retain meeting history.</label><button name='action' value='delete'>Remove API</button></form></details>")
    connections = board.connections.list()
    if connections:
        body += t('<h2>Saved APIs</h2>')
        for item in connections:
            body += "<article class='card'><a href='%s?connection=%s'>%s</a> · %s<br><small>%s</small></article>" % (base, esc(item["id"]), esc(item["name"]), t('Enabled') if item["enabled"] else t('Disabled'), esc(item["base_url"]))
    return body


def saved_settings(board, form):
    """Resolve hidden transport defaults without making the user configure model internals."""
    get = lambda key, default="": form.get(key, [default])[0]
    existing = board.connections.get(get("connection")) if get("connection") else None
    preset = PRESETS.get(get("preset"), PRESETS["custom"])
    url = endpoint_url(get("base_url"))
    if not existing:
        preset = next((p for p in PRESETS.values() if p["base_url"] == url), preset)
    defaults = existing or dict(preset, auth="none" if preset["publisher"] == "local" and not get("api_key") else "bearer")
    return {"id": get("connection") or None, "name": get("name"), "base_url": url,
            "api_key": get("api_key"), "auth": get("auth", defaults["auth"]),
            "publisher": get("publisher", defaults["publisher"]),
            "token_field": get("token_field", defaults["token_field"])}


def submit(board, token, path, form, t=None):
    t = t or localization.for_ticket(board.store, token)
    get = lambda key, default="": form.get(key, [default])[0]
    connection = get("connection")
    query = {"connection": connection}
    discovered = None
    if path == "/api/save":
        row = board.connections.save(saved_settings(board, form))
        query = {"connection": row["id"], "saved": "1"}
        notice = t('API saved.')
    elif path == "/api/discover":
        result = board.providers["custom"].discover(connection)
        discovered = result["model_ids"]
        notice = t('Read %s model IDs. Codex can configure the models; discovery does not verify inference access.') % result["total"]
    elif path == "/api/manage":
        action = get("action")
        if action == "delete" and get("delete_confirm") != "yes":
            raise BoardError("Confirm deletion of this service")
        board.connections.manage(connection, action)
        query = {} if action == "delete" else query
        notice = t('Service %s. Meeting history is preserved; changes do not transfer previous approval to another API.') % {"delete": t('deleted'), "enable": t('enabled'), "disable": t('disabled')}[action]
    elif path == "/api/remove-model":
        board.connections.remove_model(connection, get("model_id"))
        notice = t('Candidate removed. Meeting history is preserved.')
    elif path == '/api/meta-model':
        row = board.connections.get(connection)
        wire = get('wire_model')
        if not meta.is_endpoint(row['base_url']) or wire not in meta.STANDARD or not meta.access_allowed(row, wire):
            raise BoardError(t('Read the available models for this Meta account first; only listed Standard models can be added'))
        board.connections.save_model(connection, {'model': wire})
        notice = t('Muse Standard preset added. No paid inference was sent; meetings still require approval.')
    elif path == "/api/model":
        try:
            price = None
            if get("input_price") or get("output_price"):
                price = {"input_per_million": float(get("input_price")), "output_per_million": float(get("output_price")), "source": get("price_source") or "user-provided"}
            raw = {"model": get("model"), "name": get("display_name") or get("model"), "publisher": get("family"), "context_tokens": int(get("context_tokens")), "max_input_tokens": int(get("max_input_tokens")), "max_output_tokens": int(get("max_output_tokens")), "efforts": [x.strip() for x in get("efforts").split(",")], "token_field": get("model_token_field"), "parameters": json.loads(get("parameters") or "{}"), "pricing": price, "model_source": get("model_source")}
            if get("model_id"):
                raw["id"] = get("model_id")
        except (ValueError, TypeError):
            raise BoardError(t('Check numbers, prices, and JSON format; supply both input and output prices')) from None
        board.connections.save_model(connection, raw)
        notice = t('Model saved. Ask Codex to review current rankings and pricing; inference still needs separate approval.')
    else:
        raise BoardError("Unknown API settings action")
    return render(board, token, query, notice, discovered, t=t)
