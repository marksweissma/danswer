import json
import os
import re
import tarfile
import zipfile
from collections.abc import Generator
from pathlib import Path
from typing import IO, Any

from danswer.configs.app_configs import INDEX_BATCH_SIZE
from danswer.configs.constants import DocumentSource
from danswer.connectors.file.utils import (TAR_MATCH_PATTERN,
                                           check_file_ext_is_valid,
                                           get_file_ext)
from danswer.connectors.interfaces import (GenerateDocumentsOutput,
                                           LoadConnector)
from danswer.connectors.models import Document, Section
from danswer.utils.logging import setup_logger

logger = setup_logger()

_METADATA_FLAG = "#DANSWER_METADATA="


def _get_files_from_zip(
    zip_location: str | Path,
) -> Generator[tuple[str, IO[Any]], None, None]:
    with zipfile.ZipFile(zip_location, "r") as zip_file:
        for file_name in zip_file.namelist():
            with zip_file.open(file_name, "r") as file:
                yield os.path.basename(file_name), file


def _get_files_from_tar(
    tar_location: str | Path,
) -> Generator[tuple[str, IO[Any]], None, None]:
    with tarfile.open(tar_location, "r") as tar:
        for member in tar.getmembers():
            if member.isfile():
                # Open the file in the tar archive
                file = tar.extractfile(member)
                if file is not None:
                    yield os.path.basename(member.path), file


def _open_files_at_location(
    file_path: str | Path,
) -> Generator[tuple[str, IO[Any]], Any, None]:
    extension = get_file_ext(file_path)

    if extension == ".zip":
        yield from _get_files_from_zip(file_path)
    elif extension == ".txt":
        with open(file_path, "r") as file:
            yield os.path.basename(file_path), file
    elif re.search(TAR_MATCH_PATTERN, file_path.parts[-1]):
        yield from _get_files_from_tar(file_path)
    else:
        logger.warning(f"Skipping file '{file_path}' with extension '{extension}'")


def _process_file(file_name: str, file: IO[Any]) -> list[Document]:
    extension = get_file_ext(file_name)
    if not check_file_ext_is_valid(file_name):
        logger.warning(f"Skipping file '{file_name}' with extension '{extension}'")
        return []

    metadata = {}
    file_content_raw = ""
    for ind, line in enumerate(file):
        if isinstance(line, bytes):
            line = line.decode("utf-8")
        line = str(line)

        if ind == 0 and line.startswith(_METADATA_FLAG):
            metadata = json.loads(line.replace(_METADATA_FLAG, "", 1).strip())
        else:
            file_content_raw += line

    return [
        Document(
            id=file_name,
            sections=[Section(link=metadata.get("link", ""), text=file_content_raw)],
            source=DocumentSource.FILE,
            semantic_identifier=file_name,
            metadata={},
        )
    ]


class LocalFileConnector(LoadConnector):
    def __init__(
        self,
        file_locations: list[Path | str],
        batch_size: int = INDEX_BATCH_SIZE,
    ) -> None:
        self.file_locations = [Path(file_location) for file_location in file_locations]
        self.batch_size = batch_size

    def load_credentials(self, credentials: dict[str, Any]) -> dict[str, Any] | None:
        pass

    def load_from_state(self) -> GenerateDocumentsOutput:
        documents: list[Document] = []
        for file_location in self.file_locations:
            files = _open_files_at_location(file_location)

            for file_name, file in files:
                documents.extend(_process_file(file_name, file))

                if len(documents) >= self.batch_size:
                    yield documents
                    documents = []

        if documents:
            yield documents
