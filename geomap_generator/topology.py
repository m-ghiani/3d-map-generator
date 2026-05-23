from .models import OsmWay


def merge_ways_by_topology(ways: list[OsmWay]) -> list[OsmWay]:
    """Merge topologically adjacent ways into longer continuous chains.

    Ways sharing an endpoint node (by id) where exactly two ways meet
    (degree-2 node = not a junction) are joined into a single longer way.
    Junction nodes and dead ends remain as separate chain endpoints.
    """
    if len(ways) <= 1:
        return list(ways)

    # Build: node_id → set of way indices that start or end there
    at_node: dict[int, set[int]] = {}
    for idx, way in enumerate(ways):
        if len(way.geometry) < 2:
            continue
        for nid in (way.geometry[0].id, way.geometry[-1].id):
            at_node.setdefault(nid, set()).add(idx)

    def degree(node_id: int) -> int:
        return len(at_node.get(node_id, set()))

    used = [False] * len(ways)
    merged: list[OsmWay] = []

    for start_idx, way in enumerate(ways):
        if used[start_idx]:
            continue
        if len(way.geometry) < 2:
            used[start_idx] = True
            continue

        # Closed ring — keep as-is
        if way.geometry[0].id == way.geometry[-1].id:
            used[start_idx] = True
            merged.append(way)
            continue

        chain = list(way.geometry)
        used[start_idx] = True

        # Extend forward from chain tail
        extended = True
        while extended:
            extended = False
            tail_id = chain[-1].id
            if degree(tail_id) != 2:
                break
            for nxt in at_node.get(tail_id, set()):
                if used[nxt]:
                    continue
                nxt_way = ways[nxt]
                if nxt_way.geometry[0].id == tail_id:
                    chain.extend(nxt_way.geometry[1:])
                else:
                    chain.extend(reversed(nxt_way.geometry[:-1]))
                used[nxt] = True
                extended = True
                break

        # Extend backward from chain head
        extended = True
        while extended:
            extended = False
            head_id = chain[0].id
            if degree(head_id) != 2:
                break
            for nxt in at_node.get(head_id, set()):
                if used[nxt]:
                    continue
                nxt_way = ways[nxt]
                if nxt_way.geometry[-1].id == head_id:
                    chain = list(nxt_way.geometry[:-1]) + chain
                else:
                    chain = list(reversed(nxt_way.geometry[1:])) + chain
                used[nxt] = True
                extended = True
                break

        merged.append(OsmWay(id=way.id, geometry=chain, tags=way.tags))

    # Keep any skipped ways (< 2 nodes) as single-node stubs (filtered later)
    for idx, way in enumerate(ways):
        if not used[idx]:
            merged.append(way)

    return merged
