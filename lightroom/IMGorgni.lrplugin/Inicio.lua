--[[ Mientras Lightroom este abierto, cada minuto: si el script de la noche dejo la bandera
_lightroom\procesar_al_abrir, procesa la cola. Sin bandera no hace nada (abrir Lightroom de dia no
dispara ediciones). Vigila en vez de mirar una sola vez al abrir: el 7/10 la bandera estaba y la
cola no partio, y asi tambien sirve si Lightroom ya estaba abierto cuando aparece la bandera.

Solo en el catalogo Automatico: con otro catalogo abierto (el de trabajo de Renzo) la bandera se
deja quieta. El 8/10 Renzo edito la F3 a mano y una bandera vieja la habria vuelto a pisar. ]]
local LrApplication = import "LrApplication"
local LrDate = import "LrDate"
local LrFileUtils = import "LrFileUtils"
local LrTasks = import "LrTasks"

local BANDERA = "C:\\Renzo\\Proyectos\\IMGorgni\\_lightroom\\procesar_al_abrir"
local LOG = "C:\\Renzo\\Proyectos\\IMGorgni\\_lightroom\\plugin.log"
local CATALOGO = "automatico.lrcat"

local function log(msg)
	local f = io.open(LOG, "a")
	if f then
		f:write(LrDate.timeToUserFormat(LrDate.currentTime(), "%Y-%m-%d %H:%M:%S"), " inicio: ", msg, "\n")
		f:close()
	end
end

local function es_automatico()
	local ok, ruta = LrTasks.pcall(function() return LrApplication.activeCatalog():getPath() end)
	return ok and ruta and ruta:lower():sub(-#CATALOGO) == CATALOGO, ruta
end

log("plugin cargado")
LrTasks.startAsyncTask(function()
	LrTasks.sleep(30) -- que el catalogo termine de abrir
	local avisado = false
	while true do
		if LrFileUtils.exists(BANDERA) then
			local ok, ruta = es_automatico()
			if ok then
				LrFileUtils.delete(BANDERA)
				log("bandera encontrada, proceso la cola")
				local bien, err = LrTasks.pcall(function() require("Cola").procesar() end)
				if not bien then log("ERROR " .. tostring(err)) end
			elseif not avisado then
				log("bandera encontrada pero el catalogo abierto es " .. tostring(ruta) .. ": no proceso")
				avisado = true
			end
		end
		LrTasks.sleep(60)
	end
end)
