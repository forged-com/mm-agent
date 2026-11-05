from mm_agent.types import Fill, Job, Order, Resolved


def holds_order(job: Job, order: Order) -> bool:
    return order.placed < job.end and (order.ended is None or order.ended >= job.start)


def holds_fill(job: Job, fill: Fill) -> bool:
    return job.start <= fill.ts < job.end


def in_target(resolved: Resolved, record: Order | Fill) -> bool:
    return resolved.venue in (None, record.venue) and resolved.pair in (None, record.pair)
