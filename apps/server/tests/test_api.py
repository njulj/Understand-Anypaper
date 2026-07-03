def _upload(client, path):
    with path.open("rb") as handle:
        response = client.post("/api/papers", files={"file": (path.name, handle, "text/markdown")})
    assert response.status_code == 200, response.text
    return response.json()


def test_upload_and_fetch_graph(client, sample_txt):
    graph = _upload(client, sample_txt)
    paper_id = graph["paper_id"]

    assert any(node["node_type"] == "Contribution" for node in graph["nodes"])
    assert any(node["node_type"] == "Reference" for node in graph["nodes"])
    assert any(edge["edge_type"] == "CITES" for edge in graph["edges"])

    fetched = client.get(f"/api/papers/{paper_id}/graph").json()
    assert fetched == graph

    papers = client.get("/api/papers").json()
    assert [paper["paper_id"] for paper in papers] == [paper_id]


def test_blocks_references_and_evidence(client, sample_txt):
    graph = _upload(client, sample_txt)
    paper_id = graph["paper_id"]

    blocks = client.get(f"/api/papers/{paper_id}/blocks").json()
    assert blocks and all(block["text"] for block in blocks)

    references = client.get(f"/api/papers/{paper_id}/references").json()
    assert len(references) == 3

    contribution = next(node for node in graph["nodes"] if node["node_type"] == "Contribution")
    evidence = client.get(f"/api/nodes/{contribution['id']}/evidence", params={"paper_id": paper_id}).json()
    assert evidence["evidence"] and evidence["evidence"][0]["text"]


def test_pdf_document_info_and_page_rendering(client, sample_pdf):
    with sample_pdf.open("rb") as handle:
        response = client.post("/api/papers", files={"file": (sample_pdf.name, handle, "application/pdf")})
    assert response.status_code == 200, response.text
    paper_id = response.json()["paper_id"]

    document = client.get(f"/api/papers/{paper_id}/document")
    assert document.status_code == 200, document.text
    assert document.json()["pages"][0]["page"] == 1

    page = client.get(f"/api/papers/{paper_id}/document/pages/1.png")
    assert page.status_code == 200, page.text
    assert page.headers["content-type"] == "image/png"
    assert page.content.startswith(b"\x89PNG")


def test_search_and_completeness(client, sample_txt):
    graph = _upload(client, sample_txt)
    paper_id = graph["paper_id"]

    result = client.post(
        "/api/graph/search",
        json={"query": "attention", "paper_id": paper_id, "expand_depth": 1},
    ).json()
    assert result["matches"], "lexical search should match"
    assert result["matches"][0]["source"] == "lexical"
    assert result["expanded_subgraph"]["nodes"]
    assert len(result["expanded_subgraph"]["nodes"]) >= len(result["matches"])

    completeness = client.get(f"/api/papers/{paper_id}/completeness").json()
    assert completeness["scores"], "expected completeness scores for contributions"


def test_reference_analyze(client, sample_txt):
    graph = _upload(client, sample_txt)
    reference = next(node for node in graph["nodes"] if node["node_type"] == "Reference")

    analysis = client.post(f"/api/references/{reference['id']}/analyze", json={"depth": 1}).json()
    assert analysis["mentions"], "expected citation mentions in analysis"
    assert analysis["intent_summary"]
    assert "can_expand" in analysis


def test_graph_patch_roundtrip(client, sample_txt):
    graph = _upload(client, sample_txt)
    paper_id = graph["paper_id"]
    contribution = next(node for node in graph["nodes"] if node["node_type"] == "Contribution")

    patched = client.post(
        f"/api/papers/{paper_id}/graph/patch",
        json={
            "operations": [
                {"op": "update_node", "id": contribution["id"], "changes": {"title": "Verified contribution", "verified": True}},
            ]
        },
    )
    assert patched.status_code == 200, patched.text
    updated = next(node for node in patched.json()["nodes"] if node["id"] == contribution["id"])
    assert updated["title"] == "Verified contribution"
    assert updated["verified"] is True

    refetched = client.get(f"/api/papers/{paper_id}/graph").json()
    node = next(node for node in refetched["nodes"] if node["id"] == contribution["id"])
    assert node["verified"] is True

    removed = client.post(
        f"/api/papers/{paper_id}/graph/patch",
        json={"operations": [{"op": "remove_node", "id": contribution["id"]}]},
    )
    assert removed.status_code == 200
    remaining = removed.json()
    assert all(node["id"] != contribution["id"] for node in remaining["nodes"])
    assert all(
        contribution["id"] not in (edge["source_node_id"], edge["target_node_id"])
        for edge in remaining["edges"]
    )
