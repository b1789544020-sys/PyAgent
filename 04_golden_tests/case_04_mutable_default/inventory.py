def append_item(item, bucket=[]):
    bucket.append(item)
    if len(bucket) > 1:
        raise AssertionError("mutable default shared across calls")
    return bucket
