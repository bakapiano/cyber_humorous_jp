from youmo_workflow.domain.models import Contract


class FinalizePrivate(Contract):
    publication_id: str
    manifest_hash: str
