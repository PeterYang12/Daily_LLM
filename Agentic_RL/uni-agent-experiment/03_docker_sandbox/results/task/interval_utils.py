def merge_intervals(intervals):
    if not intervals:
        return []
    
    # Create a copy to avoid mutating the input
    intervals_copy = [tuple(interval) for interval in intervals]
    
    # Validate intervals and check for start > end
    for interval in intervals_copy:
        if len(interval) != 2:
            raise ValueError("Each interval must have exactly 2 elements")
        start, end = interval
        if start > end:
            raise ValueError("Interval start cannot be greater than end")
    
    # Sort intervals by start time
    intervals_copy.sort()
    
    merged = []
    for start, end in intervals_copy:
        # If merged is empty or current interval doesn't overlap with the last merged interval
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            # Merge overlapping or touching intervals
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    
    return merged
