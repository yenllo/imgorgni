--[[ Al abrir Lightroom: si el script de la noche dejo la bandera _lightroom\procesar_al_abrir,
procesa la cola sola. Sin bandera no hace nada (abrir Lightroom de dia no dispara ediciones). ]]
local LrFileUtils = import "LrFileUtils"
local LrTasks = import "LrTasks"

local BANDERA = "C:\\Renzo\\Proyectos\\IMGorgni\\_lightroom\\procesar_al_abrir"

if LrFileUtils.exists(BANDERA) then
	LrTasks.startAsyncTask(function()
		LrTasks.sleep(30) -- que el catalogo termine de abrir
		LrFileUtils.delete(BANDERA)
		require("Cola").procesar()
	end)
end
