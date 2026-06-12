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
        return RdkitInfo(False, None, "RDKit should be included in the application image. Rebuild/re-pull the current image; use the Web UI install only as a temporary repair, then restart the container.", f"{type(exc).__name__}: {exc}")
    return RdkitInfo(True, getattr(rdkit, "__version__", None), "RDKit is available from the installed application environment.")


def normalize_molfile(molfile: str | None) -> StructureNormalization:
    if not molfile:
        return StructureNormalization(None, None, None, "metadata_only")
    try:
        from rdkit import Chem  # type: ignore[import-not-found]
        from rdkit.Chem import AllChem  # type: ignore[import-not-found]
    except Exception as exc:
        return StructureNormalization(None, None, None, "rdkit_unavailable", f"{type(exc).__name__}: {exc}")
    try:
        mol = Chem.MolFromMolBlock(rdkit_molblock(molfile), sanitize=True, removeHs=False)
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
    mol = Chem.MolFromMolBlock(rdkit_molblock(query), sanitize=True, removeHs=False) if query_type == "molfile" else Chem.MolFromSmiles(query)
    if mol is None:
        return None, "RDKit could not parse query structure"
    return AllChem.GetMorganFingerprintAsBitVect(mol, 2, nBits=2048).ToBitString(), None


def substructure_match(query: str, molfile: str, query_type: str = "smarts") -> tuple[bool, str | None]:
    try:
        from rdkit import Chem  # type: ignore[import-not-found]
    except Exception as exc:
        return False, f"RDKit is not installed: {exc}"
    target = Chem.MolFromMolBlock(rdkit_molblock(molfile), sanitize=True, removeHs=False)
    if target is None:
        return False, "RDKit could not parse target molfile"
    if query_type == "smiles":
        pattern = Chem.MolFromSmiles(query)
    elif query_type == "molfile":
        pattern = Chem.MolFromMolBlock(rdkit_molblock(query), sanitize=True, removeHs=False)
    else:
        pattern = Chem.MolFromSmarts(query)
    if pattern is None:
        return False, "RDKit could not parse query structure"
    return bool(target.HasSubstructMatch(pattern)), None


def rdkit_molblock(molfile: str) -> str:
    lines = molfile.splitlines()
    counts_index = next((index for index, line in enumerate(lines) if "V2000" in line or "V3000" in line), None)
    if counts_index is None:
        return molfile
    headers = lines[:counts_index]
    if len(headers) != 3:
        headers = headers[:3] + [""] * max(0, 3 - len(headers))
    return "\n".join([*headers, *lines[counts_index:]]) + "\n"


def tanimoto(left: str | None, right: str | None) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    intersection = sum(1 for a, b in zip(left, right) if a == "1" and b == "1")
    union = sum(1 for a, b in zip(left, right) if a == "1" or b == "1")
    return intersection / union if union else 0.0


def install_rdkit() -> dict[str, Any]:
    command = [sys.executable, "-m", "pip", "install", "rdkit"]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=900, check=False)
    installed = completed.returncode == 0
    return {
        "command": " ".join(command),
        "returncode": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
        "status": "installed_restart_required" if installed else "failed",
        "restart_required": installed,
        "restart_message": "RDKit was installed into the running container. Restart the container before relying on all workers and long-lived imports." if installed else None,
        "persistence": "runtime_install_ephemeral" if installed else None,
        "persistence_message": "This Web UI installation is inside the current container filesystem. It can be lost when the image is re-pulled or the container is recreated; keep RDKit by using an image/build that includes rdkit or a persistent Python package layer." if installed else None,
        "recommended_action": "Restart this container now, then rebuild/use an image with RDKit preinstalled for durable deployments." if installed else None,
    }
