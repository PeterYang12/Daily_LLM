def merge_intervals(intervals):
    if not intervals:
        return []
    
    # Create a copy to avoid mutating the original list
    sorted_intervals = sorted(intervals)
    
    merged = []
    for interval in sorted_intervals:
        # Handle empty intervals
        if len(interval) != 2:
            raise ValueError("Each interval must contain exactly 2 elements")
        
        start, end = interval
        # Validate each interval
        if start > end:
            raise ValueError("Interval start cannot be greater than end")
        
        # If merged is empty or current interval doesn't overlap with the last merged interval
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            # Merge overlapping or touching intervals
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    
    return merged
