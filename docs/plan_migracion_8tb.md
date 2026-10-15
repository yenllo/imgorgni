# Plan de migración a 8TB (Toshiba N300)

Disco listo: **K:** (etiqueta `FirstDriveK`), GPT, NTFS 64K, 7452GB.
SMART limpio (0 horas de uso, disco nuevo).

## Por qué el vaivén

Solo hay 1 dock de 3.5" (con fuente propia) + 1 adaptador SATA de 2.5" (se
alimenta del propio USB, sin fuente). E: (HDD 1TB) y el 8TB son los dos de
3.5" → **compiten por la única bahía del dock y nunca pueden estar
conectados los dos a la vez**. G: (SSD 240GB) va por el adaptador y puede
estar conectado junto a cualquiera de los dos.

C: (SSD interno de la notebook) no compite por ninguna bahía — siempre
está disponible.

## Contenido a migrar

| Origen | Contenido | Tamaño | Cómo llega al 8TB |
|---|---|---|---|
| G: | `Fotos_F_rescatadas` | 216 GB | directo, sin vaivén (G: + 8TB conectan juntos) |
| C: | `Fotos_F_rescatadas` | 433.5 GB | directo, sin vaivén (C: siempre disponible) |
| E: | todo (~833 GB usados) | ~833 GB | **vaivén** vía G:/C:, porque E: y 8TB no coexisten |

No incluye `C:\Fotos` (12.3GB, aparte) ni `OneDrive\Pictures` (305GB,
son placeholders on-demand, no ocupan disco real — no se tocan).

## Pasos

1. **G: + 8TB conectados** → robocopy `G:\Fotos_F_rescatadas` → 8TB.
   G: queda libre para usarse como balsa.
2. **C: + 8TB conectados** (puede ser el mismo momento que el paso 1,
   da igual el orden) → robocopy `C:\Fotos_F_rescatadas` → 8TB. C: queda
   con espacio libre real para usarse como balsa también.
3. **E: conectado** (G: y/o C: disponibles como balsa) → correr
   `vaiven_8tb.py`, detecta que el 8TB no está y entra en modo **CARGAR**:
   copia el siguiente lote de E: a `_ferry_E` en G: y/o C:, hasta llenar
   el espacio libre (con margen de 5GB). No borra nada de E:.
4. Desconectar E:, conectar el 8TB → correr `vaiven_8tb.py <letra_8tb>`,
   entra en modo **DRENAR**: mueve todo lo que juntó la balsa (G:/C:) al
   8TB, respetando la ruta relativa de E:, y lo marca como hecho en
   `ferry_hecho.txt`.
5. Repetir 3-4 hasta vaciar E:. En la práctica va a ser **1-2 vueltas
   nomás**, no 4: una vez que G: y C: se vacían de sus propios
   `Fotos_F_rescatadas` (pasos 1-2), la balsa combinada queda en
   ~760GB (G: ~218GB + C: ~540GB libres), que cubre casi toda la E:
   (833GB) de una sola carga. El poco que sobre (~70-90GB) es la
   segunda vuelta, chica. Por eso conviene hacer 1-2 primero antes de
   arrancar con E:, no solo por prolijidad sino porque reduce
   directamente la cantidad de cambios de disco en el dock.

Mismo comando siempre, se corre así según lo que esté conectado en cada
momento:

```
python vaiven_8tb.py <letra_8tb>
```

## Pendiente para después (no bloquea la migración)

- **Actualizar rutas en la base de datos** (`files.file_path` y las
  cacheadas en `duplicados`): una vez que un archivo de E: pasa a vivir en
  el 8TB, la ruta vieja queda stale — mismo bug que ya se repitió varias
  veces esta sesión. Como la estructura de carpetas se preserva 1:1, el
  fix es un `UPDATE` reemplazando el prefijo de la letra vieja por la del
  8TB, corrida una vez confirmada la migración completa (o por lotes,
  cruzando contra `ferry_hecho.txt`).
- Decidir si en algún momento se borran los originales de E: (hoy el
  script solo copia, no borra nada del origen).

## Script: `vaiven_8tb.py`

Vive en `C:\Renzo\Proyectos\IMGorgni\vaiven_8tb.py`. Resumible: el
checkpoint (`ferry_hecho.txt`) vive en el mismo directorio desde donde se
corre el script.
