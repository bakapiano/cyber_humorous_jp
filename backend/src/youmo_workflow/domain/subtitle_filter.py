import unicodedata
from typing import Literal

from pydantic import Field

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.models import Contract


class DenyRule(Contract):
    id: str = Field(pattern=r"^[a-z0-9_]{1,80}$")
    aliases: list[str] = Field(min_length=1, max_length=100)
    enabled: bool = True


class WordReference(Contract):
    repository: str
    url: str
    license: str
    use: str
    commit: str | None = None


class SubtitleRules(Contract):
    schema_version: Literal[1] = 1
    version: str
    replacement: Literal["mask"] = "mask"
    description: str = ""
    rules: list[DenyRule] = Field(min_length=1, max_length=5000)
    references: list[WordReference] = Field(default_factory=list[WordReference])


def normalized(text: str) -> tuple[str, list[int]]:
    values, offsets = list[str](), list[int]()
    for index, character in enumerate(text):
        for value in unicodedata.normalize("NFKC", character).casefold():
            if not value.isspace() and unicodedata.category(value) != "Cf":
                values.append(value)
                offsets.append(index)
    return "".join(values), offsets


def ascii_word(character: str) -> bool:
    return character.isascii() and (character.isalnum() or character == "_")


class WordFilter:
    def __init__(self, rules: SubtitleRules) -> None:
        self.edges: list[dict[str, int]] = [{}]
        self.ends: dict[int, tuple[str, bool]] = {}
        for rule in rules.rules:
            if rule.enabled:
                for alias in rule.aliases:
                    self.add(alias, rule.id)

    def add(self, alias: str, rule_id: str) -> None:
        word, _ = normalized(alias)
        if not 2 <= len(word) <= 80 or "*" in word:
            raise WorkflowError("SUBTITLE_RULE_INVALID", "禁词须为2–80个字符且不能包含遮罩符*。")
        node = 0
        for character in word:
            if character not in self.edges[node]:
                self.edges[node][character] = len(self.edges)
                self.edges.append({})
            node = self.edges[node][character]
        self.ends[node] = (rule_id, word.isascii())

    def spans(self, text: str) -> list[tuple[int, int, str]]:
        value, offsets = normalized(text)
        found: list[tuple[int, int, str]] = []
        index = 0
        while index < len(value):
            match = self.longest(text, value, offsets, index)
            if match:
                end, rule_id = match
                found.append((offsets[index], offsets[end - 1] + 1, rule_id))
                index = end
            else:
                index += 1
        return found

    def longest(
        self, text: str, value: str, offsets: list[int], start: int
    ) -> tuple[int, str] | None:
        node, index, matched = 0, start, None
        while index < len(value) and value[index] in self.edges[node]:
            node = self.edges[node][value[index]]
            index += 1
            ending = self.ends.get(node)
            if ending and self.boundary(text, offsets[start], offsets[index - 1] + 1, ending[1]):
                matched = (index, ending[0])
        return matched

    @staticmethod
    def boundary(text: str, start: int, end: int, english: bool) -> bool:
        if not english:
            return True
        return (start == 0 or not ascii_word(text[start - 1])) and (
            end == len(text) or not ascii_word(text[end])
        )

    def clean(self, texts: list[str]) -> tuple[list[str], dict[str, int]]:
        counts: dict[str, int] = {}
        masked: set[int] = set()
        for start, end, rule_id in self.spans("\n".join(texts)):
            masked.update(range(start, end))
            counts[rule_id] = counts.get(rule_id, 0) + 1
        cursor, output = 0, list[str]()
        for text in texts:
            output.append(
                "".join(
                    mask_character(value) if index in masked else value
                    for index, value in enumerate(text, cursor)
                )
            )
            cursor += len(text) + 1
        return output, counts

    def clean_cues(self, texts: list[str]) -> tuple[list[str], dict[str, int]]:
        # Also review each language continuously across generated bilingual cues.
        lines = [text.partition("\n") for text in texts]
        first, counts = self.clean([row[0] for row in lines])
        second, other = self.clean([row[2] for row in lines])
        combined, final = self.clean(
            [left + row[1] + right for left, row, right in zip(first, lines, second, strict=True)]
        )
        for found in (other, final):
            for key, value in found.items():
                counts[key] = counts.get(key, 0) + value
        return combined, counts


def mask_character(character: str) -> str:
    return character if character.isspace() or unicodedata.category(character) == "Cf" else "*"
