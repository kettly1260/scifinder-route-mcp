from __future__ import annotations

import hashlib
import re
from typing import Any

from .storage import RouteStorage


CAS_RE = re.compile(r"\b\d{2,7}-\d{2}-\d\b")
INCHIKEY_RE = re.compile(r"\b[A-Z]{14}-[A-Z]{10}-[A-Z]\b")
SMILES_RE = re.compile(r"\b(?:C|N|O|S|P|F|Cl|Br|I|\[)[A-Za-z0-9@+\-\[\]\(\)=#$\\/%.]{2,}\b")


def normalize_with_rdkit(smiles: str | None) -> tuple[str | None, str | None, str | None]:
    if not smiles:
        return None, None, None
    try:
        from rdkit import Chem  # type: ignore[import-not-found]
        from rdkit.Chem import AllChem  # type: ignore[import-not-found]
    except Exception:
        return smiles, None, None
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return smiles, None, None
    canonical = Chem.MolToSmiles(mol, canonical=True)
    inchikey = Chem.MolToInchiKey(mol)
    fp = AllChem.GetMorganFingerprintAsBitVect(mol, 2, nBits=1024)
    fingerprint = fp.ToBitString()
    return canonical, inchikey, fingerprint


def extract_compound_mentions(text: str) -> list[dict[str, Any]]:
    mentions: list[dict[str, Any]] = []
    for cas in sorted(set(CAS_RE.findall(text))):
        mentions.append({"primary_name": cas, "cas": cas, "source": "text", "confidence": 0.85, "aliases": [(cas, "cas")]})
    for inchikey in sorted(set(INCHIKEY_RE.findall(text))):
        mentions.append({"primary_name": inchikey, "inchikey": inchikey, "source": "text", "confidence": 0.9, "aliases": [(inchikey, "inchikey")]})
    for smiles in sorted(set(SMILES_RE.findall(text))):
        if len(smiles) < 4 or smiles.lower() in {"yield", "with", "from", "then"}:
            continue
        canonical, inchikey, fingerprint = normalize_with_rdkit(smiles)
        mentions.append(
            {
                "primary_name": canonical or smiles,
                "smiles": smiles,
                "canonical_smiles": canonical,
                "inchikey": inchikey,
                "fingerprint": fingerprint or fallback_fingerprint(smiles),
                "source": "text",
                "confidence": 0.55,
                "aliases": [(smiles, "smiles")],
            }
        )
    return mentions


def index_reaction_compounds(storage: RouteStorage, reaction_step_id: str, text: str, *, role: str = "mentioned", source: str = "text") -> list[dict[str, Any]]:
    indexed: list[dict[str, Any]] = []
    for mention in extract_compound_mentions(text):
        aliases = mention.pop("aliases", None)
        compound = storage.upsert_compound(**mention, aliases=aliases)
        try:
            storage.link_compound_to_reaction(
                reaction_step_id,
                compound.id,
                role=role,
                confidence=float(mention.get("confidence", 0.5)),
                source=source,
            )
        except Exception:
            pass
        indexed.append(compound.to_dict())
    return indexed


def fallback_fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
