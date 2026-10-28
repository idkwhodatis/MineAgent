"""020 cold adoption links, after independently validated source/model/process graphs."""

from packages.factors.contracts import digest

SCOPE = ("owner_id", "run_id", "attempt_id", "generation")


def validate_controller_links(idx, shared, configs):
    def require(value):
        if not value:
            raise ValueError("RD protected controller cold links differ")

    def actor(launch_id, row):
        launch = idx["factor_controller_launches"].get(launch_id)
        require(launch is not None and all(launch[k] == row[k] for k in SCOPE))
        admission = shared["research_process_admissions"].get(launch["admission_id"])
        require(admission is not None)
        if admission["profile"] in {
            "mineagent.rdagent-orchestrator.v2",
            "mineagent.rdagent-orchestrator.v3",
        }:
            receipts = [
                r
                for r in shared["research_process_receipts"].values()
                if r["admission_id"] == admission["id"]
                and r["kind"] == "controller_child"
            ]
            require(len(receipts) == 1 and all(receipts[0][k] == row[k] for k in SCOPE))
        return admission

    for witness in idx["factor_source_read_witnesses"].values():
        actor(witness["controller_launch_id"], witness)
    for observation in idx["factor_generation_observations"].values():
        admission = actor(observation["controller_launch_id"], observation)
        require(
            admission["profile"]
            == (
                getattr(configs[observation["run_id"]], "controller_profile", None)
                or "mineagent.rdagent-orchestrator.v2"
            )
        )
        start = idx["factor_generation_starts"].get(observation["start_id"])
        require(start is not None and all(start[k] == observation[k] for k in SCOPE))
        sources = [
            s
            for s in idx["factor_generation_sources"].values()
            if s["start_id"] == start["id"]
        ]
        require(
            len(sources) == 1
            and start["logical_step"] == observation["logical_step"] == 0
            and configs[start["run_id"]].max_iterations == 1
        )
        source = sources[0]
        model_profile = configs[start["run_id"]].model_profile
        require(model_profile in {"fixture.rdagent.v1", "configured.rdagent.v1"})
        protocol_prefix = (
            "mineagent.rdagent-provider-"
            if model_profile == "configured.rdagent.v1"
            else "mineagent.rdagent-"
        )
        expected = {
            "schema_version": "mineagent.rdagent-adoption-observation.v1",
            "logical_step": 0,
            "evaluated_source_id": source["id"],
            "previous_adopted_source_id": None,
            "adopted_source_id": source["id"],
            "continuation_source_id": source["id"],
            "adopted_new": True,
        }
        require(
            observation["decision"] == expected
            and observation["decision_sha256"] == digest(expected)
        )
        calls = [
            c
            for c in idx["factor_upstream_calls"].values()
            if c["start_id"] == start["id"]
        ]
        require(
            any(
                c["phase"] == "knowledge_update" and c["kind"] == "embedding"
                for c in calls
            )
        )
        for call in calls:
            outcomes = [
                o
                for o in idx["factor_upstream_call_outcomes"].values()
                if o["call_id"] == call["id"]
            ]
            require(
                len(outcomes) == 1
                and outcomes[0]["status"] == "completed"
                and outcomes[0]["controller_launch_id"]
                == observation["controller_launch_id"]
            )
            members = sorted(
                (
                    m
                    for m in idx["factor_generation_model_operations"].values()
                    if m["call_id"] == call["id"]
                ),
                key=lambda m: m["request_ordinal"],
            )
            require(
                members
                and [m["request_ordinal"] for m in members] == list(range(len(members)))
            )
            responses = []
            for member in members:
                require(member["protocol"] == protocol_prefix + call["kind"] + ".v1")
                operation = shared["research_model_operations"].get(
                    member["operation_id"]
                )
                require(
                    operation is not None
                    and operation["state"] == "completed"
                    and all(operation[k] == member[k] == call[k] for k in SCOPE)
                )
                completed = []
                for outcome in shared["research_model_outcomes"].values():
                    # Frozen outcome rows bind a transport, not an operation.
                    # Traverse the actual retained FK instead of a synthetic
                    # operation_id field which no database outcome contains.
                    transport = shared["research_model_transports"].get(
                        outcome["transport_id"]
                    )
                    require(transport is not None)
                    if (
                        transport["operation_id"] == operation["id"]
                        and outcome["outcome"] == "completed"
                    ):
                        require(
                            transport["state"] == "completed"
                            and all(
                                outcome[k] == transport[k] == operation[k]
                                for k in SCOPE
                            )
                        )
                        completed.append(outcome)
                require(len(completed) == 1)
                responses.append(completed[0]["id"])
            require(
                outcomes[0]["member_count"] == len(members)
                and outcomes[0]["member_ids"] == [m["id"] for m in members]
                and outcomes[0]["response_outcome_ids"] == responses
            )
    for boundary in idx["factor_generation_boundaries"].values():
        observation = idx["factor_generation_observations"].get(
            boundary["observation_id"]
        )
        require(
            observation is not None
            and boundary["boundary"] == "returned"
            and boundary["next_logical_step"] is None
            and all(
                boundary[k] == observation[k]
                for k in (*SCOPE, "start_id", "controller_launch_id", "logical_step")
            )
        )
