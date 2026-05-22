@echo off
echo ========================================================
echo Starting TorchServe Backend for Multi-Channel Models
echo ========================================================

echo.
echo Please ensure that model_store folder contains all the .mar files.
echo The server will be listening on Port 8080.
echo.

torchserve --start --ncs --model-store model_store --ts-config config.properties

echo.
echo TorchServe has correctly started in the background.
echo You can query your models via: http://127.0.0.1:8080/predictions/[model_name_channel_x]
echo Endpoints to check server status: 
echo - http://127.0.0.1:8080/ping
echo - http://127.0.0.1:8081/models
echo.
pause
