GRID = 25

def snap(v: float) -> float:
    return round(v / GRID) * GRID