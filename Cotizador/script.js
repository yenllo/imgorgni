document.addEventListener('DOMContentLoaded', () => {
    const btnCalcular = document.getElementById('btn-calcular');
    const btnCopiar = document.getElementById('btn-copiar');
    
    btnCalcular.addEventListener('click', calcularYGenerar);
    btnCopiar.addEventListener('click', copiarTexto);

    function formatMoney(amount) {
        return new Intl.NumberFormat('es-CL', { style: 'currency', currency: 'CLP', maximumFractionDigits: 0 }).format(amount);
    }

    function diffHours(start, end) {
        if (!start || !end) return 0;
        const [h1, m1] = start.split(':').map(Number);
        const [h2, m2] = end.split(':').map(Number);
        
        let d = new Date();
        let d1 = new Date(d.getFullYear(), d.getMonth(), d.getDate(), h1, m1);
        let d2 = new Date(d.getFullYear(), d.getMonth(), d.getDate(), h2, m2);
        
        if (d2 < d1) {
            d2.setDate(d2.getDate() + 1); // Cruza la medianoche
        }
        
        return (d2 - d1) / (1000 * 60 * 60);
    }

    function calcularYGenerar() {
        // 1. Obtener valores
        const horaInicio = document.getElementById('horaInicio').value;
        const horaFin = document.getElementById('horaFin').value;
        const duracionBase = parseFloat(document.getElementById('duracionBase').value) || 0;
        const canchasSimultaneas = parseInt(document.getElementById('canchasSimultaneas').value) || 1;
        const partidosIncluidos = document.getElementById('partidosIncluidos').value;
        const ubicacion = document.getElementById('ubicacion').value;
        
        const precioBase = parseFloat(document.getElementById('precioBase').value) || 0;
        const costoTraslado = parseFloat(document.getElementById('costoTraslado').value) || 0;
        const precioHoraExtra = parseFloat(document.getElementById('precioHoraExtra').value) || 0;
        const precioCanchaExtra = parseFloat(document.getElementById('precioCanchaExtra').value) || 0;
        const precioFotoExtra = parseFloat(document.getElementById('precioFotoExtra').value) || 0;
        const anticipoPorcentaje = parseFloat(document.getElementById('anticipoPorcentaje').value) || 0;
        const emiteBoleta = document.getElementById('emiteBoleta').value;
        
        const fotosEntregadas = document.getElementById('fotosEntregadas').value;
        const fotosGaleria = document.getElementById('fotosGaleria').value;
        const tipoEdicion = document.getElementById('tipoEdicion').value;
        
        const plazoEntrega = document.getElementById('plazoEntrega').value;
        const metodoEntrega = document.getElementById('metodoEntrega').value;
        const vigenciaGaleria = document.getElementById('vigenciaGaleria').value;
        const galeriaPublica = document.getElementById('galeriaPublica').value;

        // Cálculos adicionales
        const horasCalculadas = diffHours(horaInicio, horaFin);
        let horasExtra = 0;
        if (horasCalculadas > duracionBase) {
            horasExtra = horasCalculadas - duracionBase;
        }
        
        let recargoHorasExtra = horasExtra * precioHoraExtra;
        let recargoCanchas = (canchasSimultaneas > 1) ? (canchasSimultaneas - 1) * precioCanchaExtra : 0;
        
        const total = precioBase + costoTraslado + recargoHorasExtra + recargoCanchas;
        const anticipo = Math.round(total * (anticipoPorcentaje / 100));
        const saldo = total - anticipo;

        // Actualizar UI de precios
        document.getElementById('totalFinal').innerText = formatMoney(total);
        document.getElementById('totalAnticipo').innerText = formatMoney(anticipo);
        document.getElementById('totalSaldo').innerText = formatMoney(saldo);

        // Texto de Edición
        let descEdicion = "";
        if (tipoEdicion === "Basica") {
            descEdicion = "Edición básica: ajustes de color, exposición y recorte.";
        } else if (tipoEdicion === "Completa") {
            descEdicion = "Edición completa: ajustes de color, exposición, recorte y reducción de ruido (detalles y nitidez).";
        } else {
            descEdicion = "Edición mixta según lo acordado.";
        }

        // Desglose de precios
        let desglose = `  * Precio base del pack: ${formatMoney(precioBase)}\n`;
        if (costoTraslado > 0) desglose += `  * Traslado/Estacionamiento: ${formatMoney(costoTraslado)}\n`;
        if (recargoHorasExtra > 0) desglose += `  * Recargo horas extra (${horasExtra} hrs): ${formatMoney(recargoHorasExtra)}\n`;
        if (recargoCanchas > 0) desglose += `  * Recargo canchas simultáneas (${canchasSimultaneas - 1} extra): ${formatMoney(recargoCanchas)}\n`;

        // Generar Texto
        const texto = `PROPUESTA DE COBERTURA FOTOGRÁFICA

1. DETALLES DE COBERTURA
- Ubicación: ${ubicacion}
- Fecha/Horario: Desde las ${horaInicio || '--:--'} hasta las ${horaFin || '--:--'} hrs.
- Duración total contemplada en base: ${duracionBase} horas${horasExtra > 0 ? ` (se suman ${horasExtra} hrs extras)` : ''}.
- Canchas/Ubicaciones simultáneas: ${canchasSimultaneas}.
${partidosIncluidos ? `- Partidos/Momentos incluidos: ${partidosIncluidos}` : ''}
* Importante: Traslado y estacionamiento ${costoTraslado > 0 ? 'están incluidos como ítem aparte en el precio total' : 'están incluidos en el precio base'}.

2. ENTREGABLES Y CONTENIDO DEL PACK
- Galería de Selección: Se enviará una galería privada con un mínimo de ${fotosGaleria} fotos pre-editadas.
- Cantidad a entregar: El cliente podrá seleccionar ${fotosEntregadas} fotos finales.
- Tipo de Edición: ${descEdicion}
- Fotos adicionales: Si el cliente desea más fotos de las ${fotosEntregadas} incluidas, tendrán un valor de ${formatMoney(precioFotoExtra)} cada una.
- Galería Pública extra: ${galeriaPublica === 'Si' ? 'Sí, se generará una galería adicional con contraseña y marca de agua.' : 'No, solo galería de entrega.'}

3. PRECIO Y CONDICIONES DE PAGO
- Desglose del Valor:
${desglose.trimEnd()}
- Valor Total a Cobrar: ${formatMoney(total)}
- Reserva / Anticipo (${anticipoPorcentaje}%): ${formatMoney(anticipo)} para asegurar la fecha.
- Saldo (${100 - anticipoPorcentaje}%): ${formatMoney(saldo)} a pagar a contra entrega del material final.
- Emisión de Boleta de Honorarios: ${emiteBoleta}.

4. ENTREGA DE MATERIAL
- Plazo de entrega: ${plazoEntrega} días hábiles tras la selección de fotos por parte del cliente.
- Plataforma: Entrega digital vía ${metodoEntrega}.
- Vigencia: El link de descarga estará activo por ${vigenciaGaleria} meses.

5. CONDICIONES ADICIONALES
- Extensiones: Si el evento se extiende más allá de la hora acordada (${horaFin}), se cobrará un recargo de ${formatMoney(precioHoraExtra)} por cada hora extra iniciada, más costos adicionales de estacionamiento si aplicaran.
- Cancelaciones/Reprogramaciones: En caso de cancelación por parte del cliente o clima, el anticipo no será devuelto, pero puede usarse para reprogramar la fecha (sujeto a disponibilidad). Si la cancelación es por fuerza mayor comprobable, se evaluará reprogramación sin costo.
- Uso de Material: Las fotografías entregadas son para uso privado o redes sociales del cliente. No se permite su venta o uso comercial directo sin previo acuerdo.
- Compra a posteriori: Si jugadores o terceros desean comprar fotos sueltas finalizada la selección oficial, el costo por foto extra será de ${formatMoney(precioFotoExtra)}.
`;
        
        document.getElementById('textoGenerado').value = texto;
    }

    function copiarTexto() {
        const textarea = document.getElementById('textoGenerado');
        textarea.select();
        document.execCommand('copy');
        
        const btn = document.getElementById('btn-copiar');
        const textOriginal = btn.innerText;
        btn.innerText = "¡Copiado!";
        setTimeout(() => {
            btn.innerText = textOriginal;
        }, 2000);
    }
});
