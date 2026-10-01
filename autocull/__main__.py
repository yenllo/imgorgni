"""Run Autocull as a module: python -m autocull   (python -m autocull aprender ... -> autocull.aprender)"""
import sys

if __name__ == "__main__":
    if sys.argv[1:2] == ["aprender"]:
        from autocull.aprender import main
        main(sys.argv[2:])
    else:
        from autocull.cli import main
        main()
