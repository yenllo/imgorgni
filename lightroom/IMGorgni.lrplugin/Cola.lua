--[[ Cola de trabajos de edicion automatica (1/10/2026).

Lee C:\Renzo\Proyectos\IMGorgni\_lightroom\cola.tsv. Una linea por trabajo, campos separados por TAB:

	id	origen	preset_revelado	preset_exportacion	destino	marca_de_agua	coleccion	nivelar	receta	previas	lado

- origen: una carpeta (se importan todas sus fotos, recursivo) o un .txt con una ruta por linea.
- preset_revelado: nombre del preset de Revelar a aplicar ("IMGorgni Basica"...). Vacio = no tocar
  el revelado (p. ej. exportar algo que Renzo ya termino a mano).
- preset_exportacion: title de un preset de Exportar ("LigaExScuola EdicionBasica"...). Se usan
  TODOS sus ajustes salvo la carpeta, que pasa a ser `destino`. Vacio = solo revelar, no exportar
  (las completas: Renzo recorta y borra distractores, y exporta despues).
- marca_de_agua: title de una marca de agua de Lightroom ("EdicionCompletaExScuolaMarca"). Opcional.
- coleccion: nombre de una coleccion donde dejar las fotos para revisarlas en la manana. Opcional.
- nivelar: "si" = nivelado 2D rapido del Encuadrador (Python, sin mouse): pone CropAngle y una
  calificacion de confianza (3 seguro, 2 probable, 1 revisala). Lo que no se pudo medir queda
  sin tocar y con 0 estrellas: ese es el filtro para pasarle despues la macro del boton "Automatico".

- receta: .lua de edicion_ig.py con ajustes y mascaras de IA por foto (ver cargar_receta). Opcional.
- previas: carpeta donde exportar JPEG para revisar; lado: lado largo en px (vacio = 1600, 0 = resolucion
  completa calidad 100). Antes de exportar se actualizan los ajustes de IA (actualizar_ia).

Lo terminado queda en hechos.tsv (id, ok|error, detalle, cuando): un trabajo "ok" no se repite; uno
con error se reintenta en la proxima corrida. Detalle en plugin.log. Al terminar escribe fin.txt, que
es lo que mira el script de la noche para saber que Lightroom termino.
]]
local LrApplication = import "LrApplication"
local LrDate = import "LrDate"
local LrExportSession = import "LrExportSession"
local LrFileUtils = import "LrFileUtils"
local LrPathUtils = import "LrPathUtils"
local LrTasks = import "LrTasks"

local M = {}

local function ahora() -- os.date puede no estar en el Lua de Lightroom; LrDate si
	return LrDate.timeToUserFormat(LrDate.currentTime(), "%Y-%m-%d %H:%M:%S")
end
local BASE = "C:\\Renzo\\Proyectos\\IMGorgni\\_lightroom"
local COLA = BASE .. "\\cola.tsv"
local HECHOS = BASE .. "\\hechos.tsv"
local LOG = BASE .. "\\plugin.log"
local FIN = BASE .. "\\fin.txt"
-- Dentro de Lightroom no existe os.getenv: la carpeta de datos sale del SDK (con respaldo fijo).
local function carpeta_appdata()
	local ok, ruta = pcall(LrPathUtils.getStandardFilePath, "appData")
	if ok and ruta and LrFileUtils.exists(ruta .. "\\Adobe\\Lightroom") then return ruta end
	return "C:\\Users\\renzo\\AppData\\Roaming"
end
local PRESETS = carpeta_appdata() .. "\\Adobe\\Lightroom"
-- nivelado rapido del proyecto Encuadrador (su README explica la calibracion contra Adobe)
local ENC_PY = "C:\\Renzo\\Proyectos\\Encuadrador\\.venv\\Scripts\\python.exe"
local ENC_SCRIPT = "C:\\Renzo\\Proyectos\\Encuadrador\\python_ia\\nivelar.py"
local EXT = { arw = 1, cr2 = 1, cr3 = 1, nef = 1, dng = 1, raf = 1, orf = 1, rw2 = 1,
	jpg = 1, jpeg = 1, tif = 1, tiff = 1 }

local function log(msg)
	local f = io.open(LOG, "a")
	if f then f:write(ahora(), " ", msg, "\n"); f:close() end
end

local function campos(linea) -- separa por TAB conservando los campos vacios
	local t = {}
	for c in (linea .. "\t"):gmatch("([^\t]*)\t") do t[#t + 1] = c end
	return t
end

local function lineas(ruta)
	local t = {}
	local f = io.open(ruta, "r")
	if not f then return t end
	for l in f:lines() do
		l = l:gsub("\r$", "")
		if l ~= "" and l:sub(1, 1) ~= "#" then t[#t + 1] = l end
	end
	f:close()
	return t
end

local function terminados() -- solo los "ok": los que dieron error se reintentan
	local h = {}
	for _, l in ipairs(lineas(HECHOS)) do
		local c = campos(l)
		if c[2] == "ok" then h[c[1]] = true end
	end
	return h
end

local function fotos_de(origen)
	local t = {}
	if origen:lower():match("%.txt$") then
		for _, l in ipairs(lineas(origen)) do t[#t + 1] = l end
	else
		for ruta in LrFileUtils.recursiveFiles(origen) do
			if EXT[(LrPathUtils.extension(ruta) or ""):lower()] then t[#t + 1] = ruta end
		end
	end
	table.sort(t)
	return t
end

local function preset_revelado(nombre)
	for _, carpeta in ipairs(LrApplication.developPresetFolders()) do
		for _, p in ipairs(carpeta:getDevelopPresets()) do
			if p:getName() == nombre then return p end
		end
	end
end

-- Un .lrtemplate es codigo Lua que asigna `s = {...}`. El Lua de Lightroom no trae setfenv (ni
-- os.getenv), asi que no se puede ejecutar aislado: se cambia el "s =" inicial por "return" y el
-- trozo devuelve la tabla sin tocar ninguna global.
local function plantilla(subcarpeta, titulo)
	local dir = PRESETS .. "\\" .. subcarpeta
	if not LrFileUtils.exists(dir) then return nil end
	for ruta in LrFileUtils.recursiveFiles(dir) do
		if ruta:lower():match("%.lrtemplate$") then
			local f = io.open(ruta, "r")
			local codigo = f and f:read("*a")
			if f then f:close() end
			codigo = codigo and codigo:gsub("^\239\187\191", ""):gsub("^%s*s%s*=%s*", "return ", 1)
			local fn = codigo and loadstring(codigo)
			if fn then
				local ok, s = pcall(fn)
				if ok and type(s) == "table" and s.title == titulo then return s end
			end
		end
	end
end

-- Mismo criterio que Nivelar.lua del Encuadrador: no pisa un angulo que ya exista (lo que Renzo
-- enderezo a mano o paso por la macro vale mas) y mezcla con el revelado en vez de reemplazarlo.
local function nivelar(catalogo, fotos, id)
	local lista, salida = BASE .. "\\nivelar_lista.txt", BASE .. "\\nivelar_angulos.tsv"
	local por_ruta = {}
	local f = io.open(lista, "w")
	for _, p in ipairs(fotos) do
		local r = p:getRawMetadata("path")
		por_ruta[r] = p
		f:write(r, "\n")
	end
	f:close()
	if LrFileUtils.exists(salida) then LrFileUtils.delete(salida) end
	-- cmd.exe se come las comillas externas: por eso todo el comando va entrecomillado
	local code = LrTasks.execute(string.format('""%s" "%s" "%s" "%s""', ENC_PY, ENC_SCRIPT, lista, salida))
	if code ~= 0 or not LrFileUtils.exists(salida) then error("nivelar.py fallo (codigo " .. tostring(code) .. ")") end
	local n = 0
	catalogo:withWriteAccessDo("IMGorgni: nivelar " .. id, function()
		for linea in io.lines(salida) do
			local ruta, ang, l, t, r, b, conf =
				linea:match("^(.-)\t([^\t]+)\t([^\t]+)\t([^\t]+)\t([^\t]+)\t([^\t]+)\t([^\t]+)$")
			local p = ruta and por_ruta[ruta]
			if p then
				local s = p:getDevelopSettings()
				if (s.CropAngle or 0) == 0 then
					s.CropAngle = tonumber(ang)
					s.CropLeft, s.CropTop, s.CropRight, s.CropBottom = tonumber(l), tonumber(t), tonumber(r), tonumber(b)
					p:applyDevelopSettings(s, "Nivelar")
					p:setRawMetadata("rating", tonumber(conf) or 1)
					n = n + 1
				end
			end
		end
	end, { timeout = 600 })
	return n
end

-- Receta de edicion (edicion_ig.py, 14/10/2026): un .lua que devuelve
--   { ["ruta del raw en minusculas"] = { ajustes = {...}, limpiar = bool,
--       mascaras = { { tipo = "background", valores = { local_Exposure = -0.4, ... } }, ... } } }
-- ajustes va con applyDevelopSettings; las mascaras de IA solo se pueden crear desde Revelar
-- (LrDevelopController, SDK 12+), una foto a la vez.
local function cargar_receta(ruta)
	local f = io.open(ruta, "r")
	if not f then error("no existe la receta " .. ruta) end
	local codigo = f:read("*a")
	f:close()
	local fn, err = loadstring(codigo)
	if not fn then error("receta invalida: " .. tostring(err)) end
	return fn()
end

local function tiene_mascaras(p)
	local m = p:getDevelopSettings().MaskGroupBasedCorrections
	return type(m) == "table" and next(m) ~= nil -- # da 0 si no es lista: asi la v2 apilo mascaras
end

local function aplicar_mascaras(catalogo, fotos, receta)
	local LrApplicationView = import "LrApplicationView"
	local LrDevelopController = import "LrDevelopController"
	LrApplicationView.switchToModule("develop")
	LrTasks.sleep(3)
	local n, aceptados, rechazados = 0, {}, {}
	for _, p in ipairs(fotos) do
		local r = receta[p:getRawMetadata("path"):lower()]
		if r and r.mascaras then
			catalogo:setSelectedPhotos(p, { p })
			LrTasks.sleep(4) -- que Revelar cargue la foto
			if r.limpiar then -- sin depender de tiene_mascaras: si no las ve, igual hay que borrarlas
				local ok, err = LrTasks.pcall(LrDevelopController.resetMasking)
				log("limpiar mascaras de " .. p:getFormattedMetadata("fileName") .. ": " .. tostring(ok) .. " " .. tostring(err or ""))
				LrTasks.sleep(2)
			end
			if not tiene_mascaras(p) then -- volver a correr no apila mascaras
				for _, m in ipairs(r.mascaras) do
					LrDevelopController.createNewMask("aiSelection", m.tipo)
					LrTasks.sleep(m.espera or 8) -- la IA calcula la seleccion
					for k, v in pairs(m.valores) do
						local ok = LrTasks.pcall(LrDevelopController.setValue, k, v)
						if ok then aceptados[k] = true else rechazados[k] = true end
					end
				end
				n = n + 1
			end
		end
	end
	local a, b = {}, {}
	for k in pairs(aceptados) do a[#a + 1] = k end
	for k in pairs(rechazados) do b[#b + 1] = k end
	log("mascaras: aceptados " .. table.concat(a, ",") .. " | rechazados " .. table.concat(b, ","))
	LrApplicationView.switchToModule("library")
	return n
end

-- 14/10/2026: al exportar, Lightroom frenaba con "Actualizaciones de IA recomendadas" (mascaras y ruido
-- IA recien puestos) hasta que alguien apretaba Exportar. photo:updateAISettings() (SDK 13.3) lo hace
-- antes; en un bloque de escritura propio por foto: en el mismo bloque que los ajustes no recalcula.
local function actualizar_ia(catalogo, fotos)
	local bien, mal = 0, 0
	for _, p in ipairs(fotos) do
		local ok = LrTasks.pcall(function()
			catalogo:withWriteAccessDo("IMGorgni: actualizar IA", function() p:updateAISettings() end,
				{ timeout = 300 })
		end)
		if ok then bien = bien + 1 else mal = mal + 1 end
	end
	log("ajustes de IA actualizados: " .. bien .. " ok, " .. mal .. " con error")
end

-- lado: lado largo en px (1.600 por defecto, para revisar); 0 = resolucion completa, calidad 100
local function exportar_previa(fotos, carpeta, lado)
	lado = tonumber(lado) or 1600
	LrFileUtils.createAllDirectories(carpeta)
	LrExportSession({ photosToExport = fotos, exportSettings = {
		LR_export_destinationType = "specificFolder", LR_export_destinationPathPrefix = carpeta,
		LR_export_useSubfolder = false, LR_format = "JPEG", LR_jpeg_quality = lado == 0 and 1.0 or 0.85,
		LR_export_colorSpace = "sRGB", LR_size_doConstrain = lado > 0, LR_size_resizeType = "longEdge",
		LR_size_maxHeight = lado, LR_size_maxWidth = lado, LR_size_units = "pixels", LR_size_resolution = 72,
		LR_outputSharpeningOn = false, LR_reimportExportedPhoto = false, LR_collisionHandling = "overwrite",
		LR_minimizeEmbeddedMetadata = true, LR_renamingTokensOn = false, LR_useWatermark = false,
	} }):doExportOnCurrentTask()
end

local function trabajo(catalogo, c)
	local id, origen, nombre_rev, nombre_exp, destino, marca = c[1], c[2] or "", c[3] or "", c[4] or "", c[5] or "", c[6] or ""
	local coleccion, con_nivelado = c[7] or "", (c[8] or "") == "si"
	local rutas = fotos_de(origen)
	if #rutas == 0 then error("no hay fotos en " .. origen) end
	local preset
	if nombre_rev ~= "" then
		preset = preset_revelado(nombre_rev)
		if not preset then error("no existe el preset de revelado '" .. nombre_rev .. "'") end
	end
	local exp
	if nombre_exp ~= "" then
		exp = plantilla("Export Presets", nombre_exp)
		if not exp then error("no existe el preset de exportacion '" .. nombre_exp .. "'") end
	end

	local fotos = {}
	catalogo:withWriteAccessDo("IMGorgni: importar " .. id, function()
		for _, r in ipairs(rutas) do
			local p = catalogo:findPhotoByPath(r) or catalogo:addPhoto(r)
			fotos[#fotos + 1] = p
		end
	end, { timeout = 600 })
	if preset then
		catalogo:withWriteAccessDo("IMGorgni: revelar " .. id, function()
			-- true = actualiza tambien los ajustes de IA (SDK 13.3+), si no Lightroom pide hacerlo al exportar
			for _, p in ipairs(fotos) do p:applyDevelopPreset(preset, _PLUGIN, true) end
		end, { timeout = 600 })
	end
	local detalle = #fotos .. " fotos reveladas"
	-- Modelo de color de Renzo (color_modelo.py, 8/10/2026): si editar_renato.py dejo color_<id>.tsv
	-- (ruta, temperatura, matiz, exposicion), se aplica encima del preset. Sin archivo, queda el preset.
	local tsv = BASE .. "\\color_" .. id .. ".tsv"
	if LrFileUtils.exists(tsv) then
		local valores = {}
		for _, l in ipairs(lineas(tsv)) do
			local c = campos(l)
			if c[1] and tonumber(c[2]) then valores[c[1]:lower()] = c end
		end
		local n = 0
		catalogo:withWriteAccessDo("IMGorgni: color " .. id, function()
			for _, p in ipairs(fotos) do
				local c = valores[p:getRawMetadata("path"):lower()]
				if c then
					p:applyDevelopSettings({ WhiteBalance = "Custom", Temperature = tonumber(c[2]),
						Tint = tonumber(c[3]), Exposure2012 = tonumber(c[4]) }, "Color de Renzo")
					n = n + 1
				end
			end
		end, { timeout = 600 })
		detalle = detalle .. ", color del modelo en " .. n
	end
	local receta = (c[9] or "") ~= "" and cargar_receta(c[9]) or nil
	if receta then -- antes de nivelar: si la receta trae el angulo de Renzo, nivelar no lo pisa
		catalogo:withWriteAccessDo("IMGorgni: receta " .. id, function()
			for _, p in ipairs(fotos) do
				local r = receta[p:getRawMetadata("path"):lower()]
				if r and r.ajustes then p:applyDevelopSettings(r.ajustes, "Receta IMGorgni") end
			end
		end, { timeout = 600 })
	end
	if con_nivelado then detalle = detalle .. ", " .. nivelar(catalogo, fotos, id) .. " niveladas" end
	if coleccion ~= "" then
		catalogo:withWriteAccessDo("IMGorgni: coleccion " .. coleccion, function()
			catalogo:createCollection(coleccion, nil, true):addPhotos(fotos) -- true: reusa si ya existe
		end, { timeout = 60 })
		detalle = detalle .. " en '" .. coleccion .. "'"
	end
	if receta then detalle = detalle .. ", mascaras en " .. aplicar_mascaras(catalogo, fotos, receta) end
	if (c[10] or "") ~= "" then
		actualizar_ia(catalogo, fotos)
		actualizar_ia(catalogo, fotos) -- 2a pasada: la de la F3 dejo 1 de 13 esperando un clic; si ya esta al dia tarda 1 s
		exportar_previa(fotos, c[10], c[11])
		detalle = detalle .. ", previas en " .. c[10]
	end
	if not exp then return detalle .. " (sin exportar)" end

	local ajustes = {}
	for k, v in pairs(exp.value) do ajustes["LR_" .. k] = v end
	ajustes.LR_export_destinationType = "specificFolder"
	ajustes.LR_export_destinationPathPrefix = destino
	ajustes.LR_export_useSubfolder = false
	ajustes.LR_reimportExportedPhoto = false
	ajustes.LR_collisionHandling = "overwrite" -- volver a correr un trabajo lo rehace, no duplica
	if marca ~= "" then
		local w = plantilla("Watermarks", marca)
		if not w then error("no existe la marca de agua '" .. marca .. "'") end
		ajustes.LR_useWatermark = true
		ajustes.LR_watermarking_id = w.id
	end
	LrFileUtils.createAllDirectories(destino)
	LrExportSession({ photosToExport = fotos, exportSettings = ajustes }):doExportOnCurrentTask()
	return detalle .. "; exportadas a " .. destino
end

function M.procesar()
	LrFileUtils.createAllDirectories(BASE)
	local catalogo = LrApplication.activeCatalog()
	local hechos = terminados()
	local n = 0
	for _, linea in ipairs(lineas(COLA)) do
		local c = campos(linea)
		if c[1] ~= "" and not hechos[c[1]] then
			log("inicio " .. c[1])
			local ok, res = LrTasks.pcall(trabajo, catalogo, c)
			local detalle = tostring(res):gsub("[\t\r\n]", " ")
			local f = io.open(HECHOS, "a")
			if f then
				f:write(c[1], "\t", ok and "ok" or "error", "\t", detalle, "\t", ahora(), "\n")
				f:close()
			end
			log((ok and "listo " or "ERROR ") .. c[1] .. ": " .. detalle)
			n = n + 1
		end
	end
	local f = io.open(FIN, "w")
	if f then f:write(ahora(), "\t", n, "\n"); f:close() end
	return n
end

return M
