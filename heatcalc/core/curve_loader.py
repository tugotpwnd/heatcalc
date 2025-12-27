from pathlib import Path
import csv

def load_curve_folder(folder: Path) -> dict[float, list[tuple[float, float]]]:
    """
    Loads a folder of CSVs where:
      - filename = curve parameter (e.g. 1.5.csv → key = 1.5)
      - CSV rows = x, y
    Returns:
      {curve_key: [(x1, y1), (x2, y2), ...]}
    """
    curves: dict[float, list[tuple[float, float]]] = {}

    for csv_path in folder.glob("*.csv"):
        try:
            key = float(csv_path.stem)
        except ValueError:
            raise ValueError(f"Invalid curve filename: {csv_path.name}")

        points: list[tuple[float, float]] = []

        with csv_path.open(newline="") as f:
            reader = csv.reader(f)
            for row in reader:
                if not row or row[0].startswith("#"):
                    continue
                x, y = map(float, row[:2])
                points.append((x, y))

        if not points:
            raise ValueError(f"No data in {csv_path.name}")

        curves[key] = sorted(points)

    if not curves:
        raise RuntimeError(f"No CSV files found in {folder}")

    return dict(sorted(curves.items()))
