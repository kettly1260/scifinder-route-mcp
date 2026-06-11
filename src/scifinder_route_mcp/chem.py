from __future__ import annotations

import subprocess
import sys
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class RdkitInfo:
    available: bool
    version: str | None
    install_hint: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StructureNormalization:
    smiles: str | None
    inchikey: str | None
    fingerprint: str | None
    status: str
    error: str | None = None


def rdkit_info() -> RdkitInfo:
    try:
        import rdkit  # type: ignore[import-not-found]
    except Exception as exc:
        return RdkitInfo(False, None, 'Install with: pip install -e ".[chem]" or pip install rdkit, then restart the service/container.', f"{type(exc).__name__}: {exc}")
    return RdkitInfo(True, getattr(rdkit, "__version__", None), "RDKit is available.")


def normalize_molfile(molfile: str | None) -> StructureNormalization:
    if not molfile:
        return StructureNormalization(None, None, None, "metadata_only")
    try:
        from rdkit import Chem  # type: ignore[import-not-found]
        from rdkit.Chem import AllChem  # type: ignore[import-not-found]
    except Exception as exc:
        return StructureNormalization(None, None, None, "rdkit_unavailable", f"{type(exc).__name__}: {exc}")
    try:
        mol = Chem.MolFromMolBlock(molfile, sanitize=True, removeHs=False)
        if mol is None:
            return StructureNormalization(None, None, None, "rdkit_failed", "RDKit could not parse molfile")
        smiles = Chem.MolToSmiles(mol, canonical=True)
        inchikey = Chem.MolToInchiKey(mol)
        fingerprint = AllChem.GetMorganFingerprintAsBitVect(mol, 2, nBits=2048).ToBitString()
        return StructureNormalization(smiles, inchikey, fingerprint, "indexed")
    except Exception as exc:
        return StructureNormalization(None, None, None, "rdkit_failed", f"{type(exc).__name__}: {exc}")


def fingerprint_from_query(query: str, query_type: str) -> tuple[str | None, str | None]:
    try:
        from rdkit import Chem  # type: ignore[import-not-found]
        from rdkit.Chem import AllChem  # type: ignore[import-not-found]
    except Exception as exc:
        return None, f"RDKit is not installed: {exc}"
    mol = Chem.MolFromMolBlock(query, sanitize=True, removeHs=False) if query_type == "molfile" else Chem.MolFromSmiles(query)
    if mol is None:
        return None, "RDKit could not parse query structure"
    return AllChem.GetMorganFingerprintAsBitVect(mol, 2, nBits=2048).ToBitString(), None


def substructure_match(query: str, molfile: str, query_type: str = "smarts") -> tuple[bool, str | None]:
    try:
        from rdkit import Chem  # type: ignore[import-not-found]
    except Exception as exc:
        return False, f"RDKit is not installed: {exc}"
    target = Chem.MolFromMolBlock(molfile, sanitize=True, removeHs=False)
    if target is None:
        return False, "RDKit could not parse target molfile"
    if query_type == "smiles":
        pattern = Chem.MolFromSmiles(query)
    elif query_type == "molfile":
        pattern = Chem.MolFromMolBlock(query, sanitize=True, removeHs=False)
    else:
        pattern = Chem.MolFromSmarts(query)
    if pattern is None:
        return False, "RDKit could not parse query structure"
    return bool(target.HasSubstructMatch(pattern)), None


def tanimoto(left: str | None, right: str | None) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    intersection = sum(1 for a, b in zip(left, right) if a == "1" and b == "1")
    union = sum(1 for a, b in zip(left, right) if a == "1" or b == "1")
    return intersection / union if union else 0.0


def install_rdkit() -> dict[str, Any]:
    command = [sys.executable, "-m", "pip", "install", "rdkit"]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=900, check=False)
    return {
        "command": " ".join(command),
        "returncode": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
        "status": "installed_restart_required" if completed.returncode == 0 else "failed",
        "restart_required": completed.returncode == 0,
    }
