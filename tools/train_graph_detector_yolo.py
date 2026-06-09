from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Entrena un detector de gráficos con Ultralytics YOLO.")
    parser.add_argument("--data", required=True, help="Ruta al dataset.yaml")
    parser.add_argument("--model", default="yolov8n.pt", help="Modelo base de Ultralytics")
    parser.add_argument("--imgsz", type=int, default=1024, help="Tamaño de imagen")
    parser.add_argument("--epochs", type=int, default=100, help="Número de épocas")
    parser.add_argument("--batch", type=int, default=8, help="Batch size")
    parser.add_argument("--project", default="runs/graph_detector", help="Carpeta de proyecto")
    parser.add_argument("--name", default="baseline", help="Nombre de la corrida")
    args = parser.parse_args()

    try:
        from ultralytics import YOLO
    except Exception as exc:
        print("[ERROR] No se pudo importar ultralytics.")
        print("Instala primero: pip install ultralytics")
        print(f"Detalle: {exc}")
        return 1

    data_path = Path(args.data).expanduser().resolve()
    if not data_path.exists():
        print(f"[ERROR] dataset.yaml no encontrado: {data_path}")
        return 1

    model = YOLO(args.model)
    model.train(
        data=str(data_path),
        imgsz=int(args.imgsz),
        epochs=int(args.epochs),
        batch=int(args.batch),
        project=str(Path(args.project)),
        name=str(args.name),
    )
    print("[OK] Entrenamiento lanzado correctamente.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
