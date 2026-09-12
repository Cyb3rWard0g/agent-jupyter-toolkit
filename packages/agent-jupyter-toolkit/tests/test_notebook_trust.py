import nbformat
from nbformat.sign import MemorySignatureStore, NotebookNotary

from agent_jupyter_toolkit.notebook import inspect_notebook_trust


def test_trust_inspection_uses_explicit_store_and_does_not_sign():
    notebook = nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell("1 + 1", outputs=[])])
    notary = NotebookNotary(store_factory=MemorySignatureStore, secret=b"test-secret")

    unsigned = inspect_notebook_trust(notebook, notary=notary)
    notary.sign(notebook)
    signed = inspect_notebook_trust(notebook, notary=notary)
    notebook.cells[0].source = "2 + 2"
    modified = inspect_notebook_trust(notebook, notary=notary)

    assert unsigned.signature_valid is False
    assert unsigned.cells_trusted is True
    assert signed.signature_valid is True
    assert modified.signature_valid is False
