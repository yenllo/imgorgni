# Tarea: extraer los grupos con keeper en carpeta sospechosa

Estas trabajando en `C:\Renzo\Proyectos\IMGorgni`.

Contexto: `dedup_plan.csv` es un plan de deduplicacion. Cada `grupo` junta archivos
identicos: exactamente una fila con `accion == CONSERVAR` (el keeper, el que se queda)
y una o mas con `accion == duplicado`. La columna `penalizacion` mide que tan mala es
la carpeta donde vive el archivo: 0 o -1 es una carpeta buena, y **mayor que 0 es una
carpeta sospechosa**.

Hay grupos donde todas las copias estaban en carpetas malas y se termino eligiendo la
menos mala. Esos hay que revisarlos a mano. Tu trabajo es aislarlos en un CSV.

## Restricciones (obligatorias)

- NO modifiques, muevas, renombres ni borres ningun archivo existente.
- NO toques `photos_v2.db` ni ninguno de sus backups (`photos_v2.db.backup-*`), ni
  siquiera para leerlos.
- NO modifiques `dedup_plan.csv`: es solo entrada, se abre en modo lectura.
- El unico archivo que podes crear es el entregable de abajo. Si te parece que hace
  falta escribir cualquier otra cosa, no lo hagas: reportalo como observacion y termina.

## Que hacer

1. Leé `dedup_plan.csv` (cabecera `grupo,accion,ruta,bytes,penalizacion,razon`).
2. Identificá los grupos **sospechosos**: aquellos cuya fila `CONSERVAR` tiene
   `penalizacion > 0`.
3. Para cada grupo sospechoso, emití **todas** sus filas (el keeper y todos sus
   duplicados) al CSV de salida.
4. Ordená la salida por `penalizacion` del keeper **descendente** (primero los peores),
   y dentro de cada grupo poné el keeper primero y despues sus duplicados.

El archivo pesa 27 MB. Podes agrupar en memoria si te resulta mas simple (son ~217 mil
filas, entra sin problema), pero usá el modulo `csv` de la stdlib, no pandas.

## Entregable

Un CSV llamado `revisar_keepers_sospechosos.csv` con estas columnas, en este orden:

```
grupo,accion,ruta,bytes,penalizacion,razon,penalizacion_keeper
```

Las primeras seis son las de la fila original tal cual. `penalizacion_keeper` es la
penalizacion del keeper de ese grupo, repetida en todas las filas del grupo (sirve para
ordenar y filtrar despues).

Al terminar, imprimí por stdout un JSON con exactamente estas claves:

```json
{
  "grupos": 0,
  "filas_totales": 0,
  "alternativas": 0,
  "gb": 0.0
}
```

Donde `grupos` es la cantidad de grupos sospechosos, `filas_totales` la cantidad de
filas de datos escritas al CSV, `alternativas` la cantidad de filas `duplicado`
escritas, y `gb` la suma de `bytes` de esas filas `duplicado` en GB base 1024, con dos
decimales.
