from __future__ import annotations

import ipaddress
import re
import socket
from collections import defaultdict
from typing import Any
from urllib.parse import urlparse

import httpx

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "by", "for", "from",
    "has", "have", "he", "her", "his", "i", "in", "is", "it", "its", "of",
    "on", "or", "our", "she", "that", "the", "their", "them", "they", "this",
    "to", "was", "we", "were", "will", "with", "you", "your",
}


def tokens(text: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]*", text)
        if token.lower() not in STOPWORDS and len(token) > 2
    }


def sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", text) if len(part.strip()) >= 15]


def document_text(doc: dict[str, Any]) -> str:
    return str(
        doc.get("cleaned_text")
        or doc.get("markdown")
        or doc.get("content")
        or doc.get("text")
        or ""
    )


def compare_snapshots(previous: dict[str, Any] | None,
                      current: dict[str, Any]) -> dict[str, Any]:
    if previous is None:
        return {
            "baseline_available": False,
            "current_snapshot_id": current["snapshot_id"],
            "added": current["documents"],
            "removed": [],
            "changed": [],
            "unchanged_count": 0,
        }

    def key(doc: dict[str, Any]) -> str:
        return doc.get("source_url") or doc.get("document_id") or ""

    before = {key(doc): doc for doc in previous["documents"]}
    after = {key(doc): doc for doc in current["documents"]}
    added = [after[k] for k in sorted(after.keys() - before.keys())]
    removed = [before[k] for k in sorted(before.keys() - after.keys())]
    changed = []
    unchanged = 0
    for k in sorted(before.keys() & after.keys()):
        if before[k].get("hash") != after[k].get("hash") or before[k].get("word_count") != after[k].get("word_count"):
            changed.append({"before": before[k], "after": after[k]})
        else:
            unchanged += 1
    return {
        "baseline_available": True,
        "previous_snapshot_id": previous["snapshot_id"],
        "current_snapshot_id": current["snapshot_id"],
        "added": added,
        "removed": removed,
        "changed": changed,
        "unchanged_count": unchanged,
    }


def find_possible_contradictions(documents: list[dict[str, Any]], limit: int = 50) -> list[dict[str, Any]]:
    """Conservative heuristic: flags possible conflicts, never claims definitive contradiction."""
    facts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    patterns = [
        re.compile(r"\b(?P<subject>[A-Z][A-Za-z0-9 &'/-]{2,60}?)\s+(?:is|are|costs?|charges?|includes?|supports?)\s+(?P<value>[^.!?]{2,100})", re.I),
        re.compile(r"\b(?P<subject>[A-Za-z][A-Za-z0-9 &'/-]{2,60}?)\s*:\s*(?P<value>[^.!?]{2,100})", re.I),
    ]
    for doc in documents:
        text = document_text(doc)
        for sentence in sentences(text):
            for pattern in patterns:
                match = pattern.search(sentence)
                if not match:
                    continue
                subject = re.sub(r"\s+", " ", match.group("subject").strip().lower())
                value = re.sub(r"\s+", " ", match.group("value").strip().lower())
                if len(subject.split()) > 10 or not value:
                    continue
                facts[subject].append({
                    "value": value,
                    "sentence": sentence,
                    "source_url": doc.get("source_url") or doc.get("url"),
                    "document_id": doc.get("document_id") or doc.get("id"),
                })
                break

    findings = []
    for subject, entries in facts.items():
        unique_values = {entry["value"] for entry in entries}
        if len(unique_values) < 2:
            continue
        for i, left in enumerate(entries):
            for right in entries[i + 1:]:
                if left["value"] == right["value"]:
                    continue
                lt, rt = tokens(left["value"]), tokens(right["value"])
                overlap = len(lt & rt) / max(1, len(lt | rt))
                numeric_left = set(re.findall(r"\b\d+(?:[.,]\d+)?%?\b", left["value"]))
                numeric_right = set(re.findall(r"\b\d+(?:[.,]\d+)?%?\b", right["value"]))
                negation_mismatch = ("not" in lt) != ("not" in rt)
                numeric_mismatch = bool(numeric_left and numeric_right and numeric_left != numeric_right)
                confidence = 0.35
                reasons = []
                if numeric_mismatch:
                    confidence += 0.4
                    reasons.append("different numeric values")
                if negation_mismatch:
                    confidence += 0.35
                    reasons.append("affirmation/negation mismatch")
                if overlap < 0.15 and not (numeric_mismatch or negation_mismatch):
                    continue
                confidence += min(0.2, overlap * 0.2)
                findings.append({
                    "classification": "possible_conflict",
                    "subject": subject,
                    "confidence": round(min(confidence, 0.95), 3),
                    "reasons": reasons or ["different statements for the same extracted subject"],
                    "left": left,
                    "right": right,
                })
                if len(findings) >= limit:
                    return sorted(findings, key=lambda item: item["confidence"], reverse=True)
    return sorted(findings, key=lambda item: item["confidence"], reverse=True)


def diagnose_answer(question: str, answer: str, search_payload: dict[str, Any]) -> dict[str, Any]:
    results = []
    if isinstance(search_payload, list):
        results = search_payload
    else:
        for key in ("results", "items", "records", "chunks"):
            if isinstance(search_payload.get(key), list):
                results = search_payload[key]
                break

    evidence_text = "\n".join(
        str(item.get("text") or item.get("body") or item.get("chunk_text") or "")
        for item in results
    )
    evidence_tokens = tokens(evidence_text)
    answer_sentences = sentences(answer)
    unsupported = []
    sentence_scores = []
    for sentence in answer_sentences:
        st = tokens(sentence)
        score = len(st & evidence_tokens) / max(1, len(st))
        sentence_scores.append(score)
        if score < 0.28:
            unsupported.append({"sentence": sentence, "support_score": round(score, 3)})

    overall = sum(sentence_scores) / max(1, len(sentence_scores))
    if not results:
        classification = "no_evidence"
    elif overall >= 0.65 and not unsupported:
        classification = "supported"
    elif overall >= 0.35:
        classification = "partially_supported"
    else:
        classification = "weakly_supported"

    return {
        "classification": classification,
        "support_score": round(overall, 3),
        "question": question,
        "unsupported_sentences": unsupported,
        "retrieval_result_count": len(results),
        "evidence": results,
        "note": "This is a lexical support audit, not a definitive factual judgment.",
    }


def assert_public_http_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only absolute public HTTP(S) URLs are allowed.")
    if parsed.username or parsed.password:
        raise ValueError("URLs containing credentials are not allowed.")
    if parsed.port not in {None, 80, 443}:
        raise ValueError("Only ports 80 and 443 are allowed.")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("The target hostname could not be resolved.") from exc
    for entry in addresses:
        address = ipaddress.ip_address(entry[4][0])
        if not address.is_global:
            raise ValueError("The target resolves to a non-public network address.")


def verify_web_installation(target_url: str, markers: list[str], timeout: float) -> dict[str, Any]:
    assert_public_http_url(target_url)
    with httpx.Client(timeout=timeout, follow_redirects=True, max_redirects=5) as client:
        response = client.get(target_url, headers={"User-Agent": "Nerdo-Installation-Verifier/1.0"})
    body = response.text[:2_000_000]
    present = [marker for marker in markers if marker and marker in body]
    missing = [marker for marker in markers if marker and marker not in body]
    return {
        "verified": response.status_code < 400 and not missing,
        "http_status": response.status_code,
        "final_url": str(response.url),
        "markers_present": present,
        "markers_missing": missing,
    }
