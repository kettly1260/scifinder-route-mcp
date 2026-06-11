from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

CAS_RE = re.compile(r"\b\d{2,7}-\d{2}-\d\b")


@dataclass(frozen=True)
class RdfileMolecule:
    role: str
    role_index: int
    name: str | None
    formula: str | None
    cas_rn: str | None
    molfile: str | None
    molfile_version: str | None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RdfileReactionRecord:
    record_index: int
    registry: str | None
    scheme_id: str | None
    step_id: str | None
    reactant_count: int
    product_count: int
    cas_reaction_number: str | None
    yield_text: str | None
    reagents: list[str]
    catalysts: list[str]
    solvents: list[str]
    reference: dict[str, str]
    experimental_procedure: str | None
    molecules: list[RdfileMolecule]
    fields: dict[str, str]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_rdfile_reactions(text: str) -> list[RdfileReactionRecord]:
    records = split_rdfile_records(text)
    return [parse_rdfile_reaction_record(record, index) for index, record in enumerate(records, start=1)]


def split_rdfile_records(text: str) -> list[str]:
    chunks = re.split(r"^\$RFMT\s+", text, flags=re.MULTILINE)
    return [chunk for chunk in chunks[1:] if "$RXN" in chunk or "$MOL" in chunk]


def parse_rdfile_reaction_record(record: str, index: int) -> RdfileReactionRecord:
    fields = rdfile_fields(record)
    registry, scheme_id, step_id = parse_registry_line(record)
    reactant_count, product_count = parse_rxn_counts(record)
    mol_blocks = extract_mol_blocks(record)
    warnings: list[str] = []
    if reactant_count + product_count and len(mol_blocks) < reactant_count + product_count:
        warnings.append(f"RXN counts expect {reactant_count + product_count} mol blocks but found {len(mol_blocks)}")
    molecules: list[RdfileMolecule] = []
    role_seen = {"reactant": 0, "product": 0, "unknown": 0}
    for position, molfile in enumerate(mol_blocks, start=1):
        if position <= reactant_count:
            role = "reactant"
        elif position <= reactant_count + product_count:
            role = "product"
        else:
            role = "unknown"
        role_seen[role] += 1
        meta = molfile_metadata(molfile)
        cas_rn = meta["cas_rn"] or field_value(fields, role_field_prefix(role, role_seen[role]), "CAS_RN")
        mol_warnings: list[str] = []
        dtype_cas = field_value(fields, role_field_prefix(role, role_seen[role]), "CAS_RN")
        if dtype_cas and meta["cas_rn"] and dtype_cas != meta["cas_rn"]:
            mol_warnings.append(f"Header CAS {meta['cas_rn']} differs from DTYPE CAS {dtype_cas}")
        molecules.append(
            RdfileMolecule(
                role=role,
                role_index=role_seen[role],
                name=meta["name"],
                formula=meta["formula"],
                cas_rn=cas_rn,
                molfile=molfile,
                molfile_version=meta["molfile_version"],
                warnings=mol_warnings,
            )
        )
    molecules.extend(metadata_only_molecules(fields, "reagent", "RXN:VAR(1):RGT"))
    molecules.extend(metadata_only_molecules(fields, "catalyst", "RXN:VAR(1):CAT"))
    molecules.extend(metadata_only_molecules(fields, "solvent", "RXN:VAR(1):SOL"))
    return RdfileReactionRecord(
        record_index=index,
        registry=registry,
        scheme_id=scheme_id,
        step_id=step_id,
        reactant_count=reactant_count,
        product_count=product_count,
        cas_reaction_number=fields.get("RXN:VAR(1):CAS_Reaction_Number"),
        yield_text=fields.get("RXN:VAR(1):PRO(1):YIELD"),
        reagents=field_values(fields, "RXN:VAR(1):RGT", "CAS_RN"),
        catalysts=field_values(fields, "RXN:VAR(1):CAT", "CAS_RN"),
        solvents=field_values(fields, "RXN:VAR(1):SOL", "CAS_RN"),
        reference={
            "title": fields.get("RXN:VAR(1):REFERENCE(1):TITLE", ""),
            "author": fields.get("RXN:VAR(1):REFERENCE(1):AUTHOR", ""),
            "citation": fields.get("RXN:VAR(1):REFERENCE(1):CITATION", ""),
        },
        experimental_procedure=fields.get("RXN:VAR(1):EXP_PROC"),
        molecules=molecules,
        fields=fields,
        warnings=warnings,
    )


def parse_registry_line(record: str) -> tuple[str | None, str | None, str | None]:
    first = next((line.strip() for line in record.splitlines() if line.strip()), "")
    if first.startswith("$RIREG"):
        parts = first.split()
        return first, parts[1] if len(parts) > 1 else None, parts[2] if len(parts) > 2 else None
    return None, None, None


def parse_rxn_counts(record: str) -> tuple[int, int]:
    after_rxn = record.split("$RXN", 1)[1] if "$RXN" in record else record
    before_first_mol = after_rxn.split("$MOL", 1)[0]
    for line in before_first_mol.splitlines():
        match = re.match(r"^\s*(\d+)\s+(\d+)\b", line)
        if match:
            return int(match.group(1)), int(match.group(2))
    return 0, 0


def extract_mol_blocks(record: str) -> list[str]:
    blocks: list[str] = []
    parts = record.split("$MOL")
    for part in parts[1:]:
        lines: list[str] = []
        for line in part.splitlines():
            if line.startswith("$DTYPE") or line.startswith("$RFMT") or line.startswith("$RXN"):
                break
            lines.append(line.rstrip("\r"))
            if line.strip() == "M  END":
                break
        block = "\n".join(lines).strip("\n")
        if block:
            blocks.append(block + "\n")
    return blocks


def molfile_metadata(molfile: str) -> dict[str, str | None]:
    lines = molfile.splitlines()
    counts_index = next((i for i, line in enumerate(lines) if "V2000" in line or "V3000" in line), None)
    header = [line.strip() for line in lines[: counts_index if counts_index is not None else min(4, len(lines))] if line.strip()]
    version = None
    if any("V3000" in line for line in lines[:8]):
        version = "V3000"
    elif any("V2000" in line for line in lines[:8]):
        version = "V2000"
    cas = next((match.group(0) for line in header for match in [CAS_RE.search(line)] if match), None)
    formula = next((line for line in header[1:] if re.match(r"^[A-Z][A-Za-z0-9().+-]*$", line) and not CAS_RE.search(line)), None)
    name = header[0] if header else None
    return {"name": name, "formula": formula, "cas_rn": cas, "molfile_version": version}


def rdfile_fields(record: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    parts = re.split(r"^\$DTYPE\s+", record, flags=re.MULTILINE)
    for part in parts[1:]:
        name, _, rest = part.partition("\n")
        match = re.match(r"^\$DATUM\s*(.*?)(?=^\$DTYPE\s+|\Z)", rest, flags=re.MULTILINE | re.DOTALL)
        if match:
            fields[name.strip()] = normalize_text(match.group(1))
    return fields


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def role_field_prefix(role: str, index: int) -> str:
    if role == "reactant":
        return f"RXN:RCT({index})"
    if role == "product":
        return f"RXN:PRO({index})"
    return f"RXN:{role.upper()}({index})"


def field_value(fields: dict[str, str], prefix: str, suffix: str) -> str | None:
    return fields.get(f"{prefix}:{suffix}")


def field_values(fields: dict[str, str], prefix: str, suffix: str) -> list[str]:
    return [value for key, value in sorted(fields.items()) if key.startswith(prefix) and key.endswith(f":{suffix}")]


def metadata_only_molecules(fields: dict[str, str], role: str, prefix: str) -> list[RdfileMolecule]:
    values = field_values(fields, prefix, "CAS_RN")
    return [
        RdfileMolecule(role=role, role_index=index, name=None, formula=None, cas_rn=value, molfile=None, molfile_version=None)
        for index, value in enumerate(values, start=1)
    ]
