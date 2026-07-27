"""frontend SUPPORTED_LANGUAGES(frontend/src/lib/languages.ts) <-> Judge0 language_id 매핑.

Judge0 CE 표준 language_id 기준(judge0/judge0 v1.13.x). 팀이 지원 언어를 확정하면
(docs/api-spec.md:141) 이 표만 갱신하면 된다.
"""

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class LanguageSpec:
    judge0_id: int
    compiler_options: Optional[str] = None


LANGUAGE_MAP: Dict[str, LanguageSpec] = {
    "python3": LanguageSpec(judge0_id=71),                                    # Python 3.8.1
    "cpp17": LanguageSpec(judge0_id=54, compiler_options="-std=c++17 -O2"),   # C++ (GCC 9.2.0)
    "java17": LanguageSpec(judge0_id=62),                                     # Java (OpenJDK 13.0.1)
}


def resolve_language(language: str) -> LanguageSpec:
    spec = LANGUAGE_MAP.get(language)
    if spec is None:
        raise UnsupportedLanguage(language)
    return spec


class UnsupportedLanguage(Exception):
    pass
