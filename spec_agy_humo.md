# Tarea: reporte de solo lectura sobre dedup_plan.csv

Estas trabajando en `C:\Renzo\Proyectos\IMGorgni`. Esta es una tarea de
**solo lectura y diagnostico**.

## Restricciones (obligatorias)

- NO modifiques, muevas, renombres ni borres ningun archivo.
- NO escribas archivos nuevos salvo el reporte que se pide abajo.
- NO toques `photos_v2.db` ni ninguno de sus backups, ni siquiera para leerlo.
- Si algo te parece que requiere escribir en disco fuera del reporte, no lo hagas:
  reportalo como observacion y termina.

## Que hacer

Leé `dedup_plan.csv` (CSV con cabecera `grupo,accion,ruta,bytes,penalizacion,razon`;
la columna `accion` vale `CONSERVAR` o `duplicado`) y calculá:

1. Cantidad total de filas de datos (sin contar la cabecera).
2. Cantidad de filas con `accion == CONSERVAR`.
3. Cantidad de filas con `accion == duplicado`.
4. Suma de `bytes` de las filas `duplicado`, expresada en GB (base 1024,
   con un decimal).
5. Los 5 prefijos de carpeta de primer nivel bajo `/mnt/fotos/` que mas
   aparecen entre las filas `duplicado`, con su conteo. Ejemplo de prefijo:
   para `/mnt/fotos/OneDrive_bajado/algo/foo.jpg` el prefijo es `OneDrive_bajado`.

Usá Python para el calculo, no lo estimes ni lo infieras. El archivo pesa 27 MB:
procesalo en streaming con el modulo `csv`, no lo cargues entero en memoria.

## Entregable

Escribí el resultado en `agy_humo_resultado.json`, con exactamente estas claves:

```json
{
  "filas_total": 0,
  "filas_conservar": 0,
  "filas_duplicado": 0,
  "gb_duplicados": 0.0,
  "top5_carpetas": [["nombre", 0]]
}
```

Y ademas imprimí ese mismo JSON por stdout al terminar.
