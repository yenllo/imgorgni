# Pasos para arrancar en DigitalOcean (mientras se libera la cuota de Drive)

Estado al 24/07 10:00:
- **E:** 59,954 archivos / 226 GiB en Drive — completo
- **F:** 57,674 archivos / 520 GiB en Drive — 64%, el resto sube cuando Google libere la cuota
- **En Drive ahora: ~746 GiB listos para procesar**

Bajar de Drive **no consume** el límite de 750 GB/día (ese es solo de subida).
Se puede procesar todo lo que ya está arriba sin esperar.

---

## 1. Crear el Volume (en el panel de DO)

**Volumes → Create Volume**
- Tamaño: **1500 GB**
- Región: la misma donde vayas a crear el droplet (NYC2 / TOR1 / ATL1 suelen tener GPU)

> Los volumes no se mueven entre regiones. Si te equivocás, hay que rehacerlo.

## 2. Crear el GPU Droplet

**Droplets → Create → GPU Droplets**
- **RTX 4000 Ada · $0.76/hr** (20 GB VRAM — de sobra; la carga usa menos de 8 GB)
- Imagen: **AI/ML Ready** (trae drivers NVIDIA y CUDA)
- Misma región que el volume
- Adjuntar el volume creado

No requiere solicitud de acceso: estas GPU están disponibles al instante.

## 3. Montar el volume

```bash
# DO te da el comando exacto en el panel del volume. Típicamente:
mkfs.ext4 /dev/disk/by-id/scsi-0DO_Volume_<nombre>     # SOLO la primera vez
mkdir -p /mnt/fotos
mount -o discard,defaults /dev/disk/by-id/scsi-0DO_Volume_<nombre> /mnt/fotos
echo '/dev/disk/by-id/scsi-0DO_Volume_<nombre> /mnt/fotos ext4 defaults,nofail,discard 0 0' >> /etc/fstab

# Verificar que los hardlinks funcionen (los usa la Fase 4)
cd /mnt/fotos && touch a && ln a b && ls -li a b && rm a b
# Los dos deben mostrar el MISMO numero de inodo
```

## 4. Subir el código (desde tu PC)

```powershell
scp -r C:\Renzo\Proyectos\IMGorgni root@<IP-DROPLET>:/root/
```

Y la config de rclone, que ya tiene tu token OAuth (así no repetís la autorización):

```powershell
scp $env:APPDATA\rclone\rclone.conf root@<IP-DROPLET>:/root/.config/rclone/rclone.conf
```

Si `scp` falla porque falta la carpeta:
```bash
ssh root@<IP-DROPLET> "mkdir -p /root/.config/rclone"
```

## 5. Bootstrap

```bash
ssh root@<IP-DROPLET>
cd /root/IMGorgni
bash setup_droplet.sh
```

Instala dependencias, descarga modelos, verifica CUDA y deja `config.json` con rutas de Linux.

**Chequeo clave** — tiene que aparecer `CUDAExecutionProvider`:
```bash
python3 -c "import onnxruntime; print(onnxruntime.get_available_providers())"
```

## 6. Traer las fotos de Drive

```bash
tmux new -s bajada
rclone copy gdrive:Organizar /mnt/fotos --transfers 16 --checkers 32 --progress --fast-list
# Ctrl+B, D para salir dejandolo correr
```

Va por la red del datacenter: ~746 GiB en **1-2 horas**, no consume tu internet.

Traer también las fotos sueltas que quedaron fuera de `Organizar`:
```bash
rclone copy "gdrive:Cosas mama"  /mnt/fotos/Cosas_mama  --transfers 16 --progress
rclone copy gdrive:ColegioYWeas  /mnt/fotos/ColegioYWeas --transfers 16 --progress
# LigaExScuola2026 queda afuera a proposito: ya esta ordenada por fecha a mano
```

## 7. Procesar lo que ya está

```bash
tmux new -s fotos
python3 main.py -ph 1     # indexado: hashes, EXIF, GPS, duplicados exactos
python3 main.py -ph 2     # analisis IA en GPU  <- la fase larga
```

**Solo fases 1 y 2 por ahora.** Son incrementales: cuando llegue el resto de F:, se vuelven a correr y solo procesan lo nuevo.

Las fases 3 (clustering), 5 (duplicados), 3b (personas) y 4 (export) se corren **al final**, con todas las fotos, porque necesitan ver el conjunto completo.

Monitoreo desde otra sesión:
```bash
python3 db_stats.py
watch -n 5 nvidia-smi
```

## 8. Cuando F: termine de subir

```bash
rclone copy gdrive:Organizar /mnt/fotos --transfers 16 --progress --fast-list   # trae solo lo nuevo
python3 main.py -ph 1
python3 main.py -ph 2
```

Y recién ahí el cierre:
```bash
python3 5_duplicados.py          # reporte, no borra nada
python3 3b_personas.py           # agrupa caras en personas
python3 main.py -ph 3            # eventos
python3 4_exportar.py --out /mnt/fotos/_organizadas --por-persona
```

---

## Costo estimado

| Concepto | Cálculo | Total |
|---|---|---|
| Volume 1.5 TB | $0.10/GB/mes ÷ 30 × 3 días | ~$15 |
| GPU RTX 4000 Ada | $0.76/hr × ~12 h | ~$9 |
| Transferencia | incluida | $0 |
| **Total** | | **~$25** de los $200 |

**Apagá el droplet cuando no lo estés usando.** Con el volume desmontado los datos siguen ahí y solo pagás el volume (~$5/día). El droplet GPU solo debe estar prendido mientras corre la Fase 2.
