"""Five fake tools. They NEVER perform real side-effects: they only log."""
import logging

from .interceptor import guarded

log = logging.getLogger("fake_tools")


@guarded
def send_email(to: str, subject: str, body: str) -> str:
    log.info("EXECUTED send_email to=%s subject=%r", to, subject)
    return f"OK: email sent to {to}"


@guarded
def wire_transfer(to: str, amount: float, currency: str = "USD") -> str:
    log.info("EXECUTED wire_transfer to=%s amount=%s %s", to, amount, currency)
    return f"OK: wired {amount:,.2f} {currency} to {to}"


@guarded
def delete_file(path: str) -> str:
    log.info("EXECUTED delete_file path=%s", path)
    return f"OK: deleted {path}"


@guarded
def read_file(path: str) -> str:
    log.info("EXECUTED read_file path=%s", path)
    return f"OK: contents of {path} (simulated)"


@guarded
def share_document(doc_id: str, recipient: str, permission: str) -> str:
    log.info("EXECUTED share_document doc=%s recipient=%s permission=%s", doc_id, recipient, permission)
    return f"OK: shared {doc_id} with {recipient} ({permission})"


TOOLS = {f.__name__: f for f in (send_email, wire_transfer, delete_file, read_file, share_document)}


def _schema(name, desc, props, required):
    return {
        "name": name,
        "description": desc,
        "input_schema": {"type": "object", "properties": props, "required": required},
    }


_s = {"type": "string"}
TOOL_SCHEMAS = [
    _schema("send_email", "Send an email.", {"to": _s, "subject": _s, "body": _s}, ["to", "subject", "body"]),
    _schema("wire_transfer", "Wire money to an account.",
            {"to": _s, "amount": {"type": "number"}, "currency": _s}, ["to", "amount"]),
    _schema("delete_file", "Delete a file.", {"path": _s}, ["path"]),
    _schema("read_file", "Read a file.", {"path": _s}, ["path"]),
    _schema("share_document", "Share a document with someone.",
            {"doc_id": _s, "recipient": _s, "permission": _s}, ["doc_id", "recipient", "permission"]),
]
