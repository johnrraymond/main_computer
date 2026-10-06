from tools.mother_preflight_paranoia2 import evaluate_target_node_service_rows


def _target():
    return {
        "node": "mainneta-super1",
        "controller_id": "coolify-a",
        "service_uuid": "target-uuid",
    }


def test_excluded_helper_hint_for_removed_node_is_diagnostic_not_topology_poison():
    result = evaluate_target_node_service_rows(
        target=_target(),
        expected_nodes=["mainneta-super1", "mainneta-super2", "mainnetc-super1"],
        inventory_hints=[
            {
                "uuid": "old-helper",
                "name": "mother-add-node-validator-admission-voter-mainnetc-super2-20261005t224016",
                "description": "Ephemeral Mother add-node validator admission voter guardian",
                "status": "running:unhealthy:excluded",
                "node_hints": ["mainnetc-super2"],
            }
        ],
    )

    assert result["clean"] is True
    assert result["status"] == "pass"
    assert result["conflicting_rows"] == []
    assert result["unexpected_node_rows"] == []


def test_helper_that_mentions_target_does_not_block_post_remove_topology():
    result = evaluate_target_node_service_rows(
        target=_target(),
        expected_nodes=["mainneta-super1", "mainneta-super2", "mainnetc-super1"],
        inventory_hints=[
            {
                "uuid": "helper-uuid",
                "name": "mother-add-node-validator-activation-guardian",
                "description": "guardian for mainneta-super1",
                "status": "running:healthy:excluded",
                "node_hints": ["mainneta-super1"],
            }
        ],
    )

    assert result["clean"] is True
    assert result["conflicting_rows"] == []
    assert len(result["target_rows"]) == 1
    assert result["target_rows"][0]["topology_authoritative_primary"] is False
    assert result["target_rows"][0]["would_poison_post_remove_topology"] is False


def test_duplicate_exact_target_primary_row_still_blocks():
    result = evaluate_target_node_service_rows(
        target=_target(),
        expected_nodes=["mainneta-super1", "mainneta-super2", "mainnetc-super1"],
        inventory_hints=[
            {
                "uuid": "target-uuid",
                "name": "mainneta-super1",
                "description": "primary",
                "status": "running:healthy",
                "node_hints": ["mainneta-super1"],
            },
            {
                "uuid": "duplicate-uuid",
                "name": "mainneta-super1",
                "description": "duplicate primary",
                "status": "running:healthy",
                "node_hints": ["mainneta-super1"],
            },
        ],
    )

    assert result["clean"] is False
    assert len(result["conflicting_rows"]) == 1
    row = result["conflicting_rows"][0]
    assert row["exact_primary_node"] == "mainneta-super1"
    assert row["topology_authoritative_primary"] is True
    assert row["would_poison_post_remove_topology"] is True


def test_exact_unexpected_primary_node_still_blocks_current_topology():
    result = evaluate_target_node_service_rows(
        target=_target(),
        expected_nodes=["mainneta-super1", "mainneta-super2", "mainnetc-super1"],
        inventory_hints=[
            {
                "uuid": "unexpected-primary",
                "name": "mainnetc-super2",
                "description": "primary",
                "status": "running:healthy",
                "node_hints": ["mainnetc-super2"],
            }
        ],
    )

    assert result["clean"] is False
    assert len(result["unexpected_node_rows"]) == 1
    row = result["unexpected_node_rows"][0]
    assert row["exact_primary_node"] == "mainnetc-super2"
    assert row["would_poison_current_topology"] is True
