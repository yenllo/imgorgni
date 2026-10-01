"""
Punto de entrada del pipeline: python main.py -ph {1,2,3} [-f] [-c config.json]
"""
import argparse
import importlib

PHASES = {
    1: "1_indexado",
    2: "2_analisis",
    3: "3_agrupacion",          # el DBSCAN global quedó en 3_agrupacion_mejorado
}


def main():
    parser = argparse.ArgumentParser(description="Organizador de Fotos IA")
    parser.add_argument("-ph", "--phase", type=int, choices=[1, 2, 3], required=True,
                        help="Fase: 1=indexado, 2=análisis IA, 3=agrupación")
    parser.add_argument("-f", "--force", action="store_true",
                        help="Reprocesar desde cero")
    parser.add_argument("-c", "--config", default="config.json",
                        help="Ruta al archivo de configuración")
    parser.add_argument("--no-mover", action="store_true",
                        help="Fase 1: indexar y hashear sin mover ningún archivo. "
                             "Los duplicados exactos, marcas de agua y capturas se "
                             "indexan igual y quedan para la Fase 5 y el curador.")
    args = parser.parse_args()

    module = importlib.import_module(PHASES[args.phase])
    kwargs = {"config_path": args.config, "force": args.force}
    if args.no_mover:
        if args.phase != 1:
            parser.error("--no-mover solo aplica a la fase 1 (es la única que mueve)")
        kwargs["no_mover"] = True
    module.run(**kwargs)


if __name__ == "__main__":
    main()
