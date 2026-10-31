# 墨迹 Ink Studio · ESP32-C3 墨水屏图片工作台

基于 **ESP32-C3 Super Mini + ESP-IDF 5.4** 的黑白红墨水屏项目，包含桌面图片处理工具、USB/Wi-Fi 图片接收固件，以及一个独立的单图显示示例。

在电脑上导入图片，调整裁切和黑白红效果，通过 USB 或 Wi-Fi 发送给 C3 完成全屏刷新。可以把一张图片保存到设备 Flash，之后直接重绘。

当前适配 **新 PCB Board2 / Schematic2** 与 **E2213JS0C1/C2，104 × 212 黑白红屏**。固件使用 ESP-IDF C 实现，不依赖 Arduino。

## 目录

- [功能与当前状态](#功能与当前状态)
- [工程结构](#工程结构)
- [硬件与接线](#硬件与接线)
- [快速开始](#快速开始)
- [首次安装接收固件](#首次安装接收固件)
- [从源码运行上位机](#从源码运行上位机)
- [编译与烧录固件](#编译与烧录固件)
- [图片处理与数据格式](#图片处理与数据格式)
- [保存与重绘](#保存与重绘)
- [屏幕驱动流程](#屏幕驱动流程)
- [Wi-Fi 无线传图](#wi-fi-无线传图接收固件-12)
- [USB 通信协议](#usb-通信协议)
- [测试与打包](#测试与打包)
- [常见问题](#常见问题)
- [后续开发方向](#后续开发方向)
- [参考资料与许可证](#参考资料与许可证)

## 功能与当前状态

| 模块 | 已实现内容 |
| --- | --- |
| 图片导入 | JPG、PNG、BMP、WebP；文件选择与拖放 |
| 构图 | 横竖屏、等比例完整显示、铺满裁切、旋转、缩放、平移 |
| 颜色处理 | 亮度、对比度、黑色阈值、红色识别、黑白抖动、关闭红色色层 |
| 预览与导出 | 黑白红预览；导出 PNG、C 数组、原始 BIN |
| USB / Wi-Fi 连接 | COM 端口扫描、Wi-Fi 热点 HTTP 传图、固件握手、请求 ID、CRC32 校验、连接错误提示 |
| 屏幕刷新 | 写入图像、开启升压、全刷、关闭升压；显示阶段进度 |
| 设备保存 | NVS 保存一张图片，覆盖旧图；可重绘已存图片 |
| 异常处理 | BUSY 超时、异常过快刷新、存储不可用、串口占用及写入超时提示 |
| 模拟设备 | 无硬件体验握手、传图、保存与重绘 |
| Windows 分发 | PyInstaller 打包，可运行 EXE 自检，交付包文件校验 |

**当前边界：**只实现全屏刷新；没有局刷、自动轮播、路由器联网模式、蓝牙传图、电池管理或 ESP32 深度休眠。接收固件保持 USB 和 Wi-Fi 热点在线，刷新结束关闭屏幕 DC/DC。

开发验证环境为 **Windows x64、Python 3.13.14、ESP-IDF 5.4.4**。接收固件当前版本为 **1.2**，已通过构建、21 项软件测试及打包 EXE 模拟自检；用户已反馈新版可用。自动测试不覆盖实屏成像、供电稳定性或长期运行，详细范围见 [验证记录](image_tool/验证记录.md)。

## 工程结构

仓库中有 **两个独立 ESP-IDF 工程**。使用上位机时，应烧录 `image_tool/firmware` 中的接收固件。

```text
.
├── README.md                         # 项目总说明
├── .gitignore                        # 排除本地环境与构建产物
├── CMakeLists.txt                    # 单图显示工程
├── sdkconfig.defaults               # 单图工程默认配置
├── main/
│   ├── main.c                       # 上电显示内置图片
│   ├── epd_image.h                  # 内置黑/红点阵
│   └── CMakeLists.txt
├── assets/                          # 单图示例原图与转换预览
├── tools/
│   ├── convert_image.ps1            # 单图图片转换脚本
│   └── package_delivery.py          # 生成源码与上位机交付 ZIP
├── build_idf54.ps1                  # 原电脑的单图构建入口
├── 交付说明.md                      # 接收方安装、开发与交付说明
└── image_tool/
    ├── app.py                       # Qt 图形界面
    ├── image_core.py                # 图像处理、色层打包与导出
    ├── wifi_device.py               # HTTP 工作线程、进度轮询
    ├── test_wifi.py                 # HTTP 与 Qt 端到端模拟测试
    ├── protocol.py                  # USB 行协议编码与解析
    ├── device.py                    # 串口工作线程与模拟设备
    ├── requirements.txt             # 上位机运行依赖
    ├── setup.ps1                    # 创建虚拟环境并安装依赖
    ├── 启动上位机.cmd               # 优先启动 EXE，否则启动源码
    ├── package.ps1                  # 打包与 EXE 自检
    ├── test_core.py                 # 图像与协议测试
    ├── test_gui.py                  # Qt 流程测试
    ├── test_device.py               # 串口错误处理测试
    ├── assets/                      # 上位机示例图片
    ├── 使用说明.md                  # 上位机详细说明
    └── firmware/
        ├── CMakeLists.txt           # 独立 USB/Wi-Fi 接收工程
        ├── sdkconfig.defaults       # 接收工程默认配置
        ├── build_firmware.ps1       # 编译并生成固件包
        ├── flash_firmware.ps1       # 校验与烧录预编译固件
        ├── make_bundle.py           # 提取固件、偏移及 SHA256
        ├── main/
        │   ├── receiver.c           # 共用命令校验、刷新任务、NVS
        │   ├── wifi_transport.c     # 热点、HTTP、会话事件日志
        │   ├── Kconfig.projbuild    # 热点名称和密码
        │   ├── epd_driver.c         # SPI、初始化与刷新
        │   └── epd_driver.h
        └── bin/                    # 预编译接收固件与 bundle.json
```

`.venv/`、`build/`、`dist/`、`logs/` 等目录在本地运行或构建时生成，不需要提交到 GitHub。PCB 工程和屏幕厂家数据手册目前不包含在软件源码包中。

## 硬件与接线

### 所需硬件

- ESP32-C3 Super Mini 开发板。
- 与本项目匹配的新 PCB Board2，包括屏幕连接器及外围驱动、升压电路。
- E2213JS0C1/C2 黑白红墨水屏。
- 支持数据传输的 USB 线及与 PCB 匹配的供电。

屏幕刷新依赖 PCB 的外围升压电路。表中的 3V3 为接口供电，不是屏幕内部所有驱动电压；接线和供电应以实际 PCB 原理图为准。

### 新板 H3 接口

| H3 引脚号 | 信号 | ESP32-C3 连接 | 说明 |
| --- | --- | --- | --- |
| 1 | BUSY_N | GPIO3 | 0 表示忙，1 表示就绪 |
| 2 | RST_N | GPIO1 | 屏幕复位，低有效 |
| 3 | DC | GPIO5 | 命令 / 数据选择 |
| 4 | CSB | GPIO7 | SPI 片选，低有效 |
| 5 | SCL / SCLK | GPIO4 | SPI 时钟 |
| 6 | SDA / MOSI | GPIO6 | SPI 数据输出 |
| 7 | 3V3 | 3.3 V 电源 | 接口供电 |
| 8 | GND | GND | 共地 |

H3 的引脚号不代表照片中的左右顺序，请根据 PCB 的 1 脚标记确认方向。RST_N 是屏幕复位信号，区别于 C3 开发板自身的 EN / RESET。

SPI 使用 **Mode 0、MSB first、默认 1 MHz、手动 CS、无 MISO**。GPIO18/19 留给 C3 原生 USB。

旧 PCB 曾使用 RST=4、SCLK=6、MOSI=7、CS=10，不能直接套用到新板。

### USB 与供电

上位机数据连接必须接到 **ESP32-C3 Super Mini 自身的 USB 口**。旁边仅用于电源的接口不能代替 USB 数据连接。屏幕供电沿用已验证可正常工作的接法，所有信号共地。

## 快速开始

**从 GitHub 获取源码：**下载本仓库 ZIP 或执行 `git clone https://github.com/Q-tar/Three-Color-Ink-Studio.git`，然后按[从源码运行上位机](#从源码运行上位机)安装依赖并启动。仓库包含接收固件源码和预编译固件，不包含 Windows EXE；EXE 由维护者按[测试与打包](#测试与打包)生成后单独分发。

### 已有 Windows 使用包

1. 完整解压“墨水屏上位机使用包”。
2. 打开 `InkStudio-WiFi/InkStudio-WiFi.exe`，保留旁边的 `_internal`、`firmware` 等目录。
3. 用 USB 数据线连接 C3，保证屏幕供电正常。
4. 首次使用先安装配套接收固件，见下一节。
5. 点击“扫描”，选择实际 COM 端口，点击“连接设备”。
6. 握手成功后导入图片，调整预览，点击“发送到墨水屏”。
7. 等待刷新结束，再进行下一次操作。

**运行使用包不需要安装 Python、ESP-IDF 或 Arduino。** 首次烧录固件所需工具另行安装；板子已预装接收固件时，可以直接连接使用。

使用包由维护者通过下文的打包命令生成；GitHub 的源码 ZIP 本身不包含预打包 EXE。

### 没有硬件也可以体验

端口列表选择“模拟设备 · 不连接硬件”，连接后尝试导入、发送、保存和重绘。

模拟流程约 2.3 秒，不访问真实 COM 端口；模拟保存仅在当前连接期间有效。模拟成功用于验证软件交互，不代表真实屏幕完成刷新。

## 首次安装接收固件

根目录的单图显示固件不会接收上位机图片。首次连接前，需要将程序替换为 `image_tool/firmware` 的配套 USB/Wi-Fi 接收固件。

下文 **COM9 都是示例**，请替换为设备管理器中的实际端口。烧录前关闭上位机连接，以及占用端口的 IDF / Arduino / VS Code 串口监视器。

### 方式一：普通 Python + esptool

安装 Windows Python，推荐已验证的 3.13 x64，并将 Python 加入 PATH。在 PowerShell 中执行：

```powershell
python -m pip install esptool==5.2.0
```

从仓库根目录烧录预编译固件：

```powershell
powershell -ExecutionPolicy Bypass -File .\image_tool\firmware\flash_firmware.ps1 -Port COM9
```

如果拿到的是上位机使用包，在解压后的 `InkStudio-WiFi` 目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\firmware\flash_firmware.ps1 -Port COM9
```

多套 Python 并存时，可追加 `-PythonPath "实际的python.exe完整路径"`，指定已经安装 esptool 的 Python。也支持 `-EspToolPath` 指定 `esptool.py`。

仅检查工具路径和固件 SHA256，不打开串口、不烧录：

```powershell
powershell -ExecutionPolicy Bypass -File .\image_tool\firmware\flash_firmware.ps1 -Port COM9 -CheckOnly
```

### 方式二：已有 ESP-IDF 环境

打开 **ESP-IDF 5.4.x PowerShell 终端**，进入仓库根目录，执行同一烧录脚本。脚本优先识别 IDF 环境中的 Python 和 esptool。

也可在上位机点击“首次安装接收固件”。该按钮启动配套 PowerShell 脚本，仍需要电脑能找到工具；若上位机环境找不到工具，可先用命令行完成一次安装。

烧录成功后重新扫描端口再连接。**接收固件上电不会主动刷屏**，收到传图或重绘指令才会刷新。

### 预编译固件

固件与地址记录在 [bundle.json](image_tool/firmware/bin/bundle.json)，脚本逐个校验 SHA256。

| 文件 | 烧录偏移 |
| --- | --- |
| `bootloader.bin` | `0x0` |
| `partition-table.bin` | `0x8000` |
| `epd_receiver.bin` | `0x10000` |

当前配置：ESP32-C3、DIO、80 MHz、2 MB Flash。修改分区或 Flash 配置后，应重新编译并生成匹配的固件包。

## 从源码运行上位机

### 依赖

| 依赖 | 当前版本 | 用途 |
| --- | --- | --- |
| Python | 验证版本 3.13.14 x64 | 运行环境 |
| PySide6 | 6.12.0 | Qt 图形界面 |
| Pillow | 13.0.0 | 图片读取与处理 |
| pyserial | 3.5 | 串口通信 |
| PyInstaller | 6.22.3，打包时安装 | 生成 Windows EXE |

运行依赖固定在 [requirements.txt](image_tool/requirements.txt)。esptool 仅在烧录时需要，不属于图片编辑功能的运行依赖。

### 安装与启动

将仓库下载或克隆到本地，建议使用较短的英文路径，例如 `D:\epd_project`。以下命令从仓库根目录开始：

```powershell
cd .\image_tool
powershell -ExecutionPolicy Bypass -File .\setup.ps1
.\.venv\Scripts\python.exe .\app.py
```

`setup.ps1` 创建本目录 `.venv` 并安装固定依赖。之后可双击 `启动上位机.cmd`；该入口优先使用本地打包 EXE，找不到时使用虚拟环境中的源码。

如需确认运行的是最新修改的源码，直接执行 `.\.venv\Scripts\python.exe .\app.py`。

## 编译与烧录固件

安装 **ESP-IDF 5.4.x** 及 ESP32-C3 工具链，推荐 5.4.4。使用安装器提供的 ESP-IDF PowerShell 终端，先确认：

```powershell
idf.py --version
```

若提示找不到 `idf.py`，当前终端尚未加载环境。先打开 ESP-IDF 终端或加载自己电脑安装位置下的环境脚本，再运行后续命令。

### USB/Wi-Fi 接收固件：配合上位机使用

在仓库根目录执行：

```powershell
cd .\image_tool\firmware
powershell -ExecutionPolicy Bypass -File .\build_firmware.ps1
powershell -ExecutionPolicy Bypass -File .\flash_firmware.ps1 -Port COM9
```

构建脚本检查 IDF 版本、编译工程，并运行 `make_bundle.py` 更新 `bin/` 中的固件、偏移地址和 SHA256 清单。

也可一次完成编译与烧录：

```powershell
powershell -ExecutionPolicy Bypass -File .\build_firmware.ps1 -Flash -Port COM9
```

普通 PowerShell 中可通过 `-IdfProfile "你的IDF环境脚本完整路径"` 显式加载自己的环境。脚本中的原电脑路径仅作为兼容回退，不要求其他电脑使用相同的 C/D 盘目录。

接收工程通过 USB Serial/JTAG 驱动收发协议，Wi-Fi 热点与 HTTP 接收同时启用，普通日志配置在 UART0，避免与 USB 回复混用。使用上位机时不要同时让串口监视器占用 USB COM 端口。

### 单图显示固件：上电显示内置图片

回到仓库根目录，在 ESP-IDF 终端执行：

```powershell
idf.py build
idf.py -p COM9 flash monitor
```

退出 IDF monitor：`Ctrl+]`。

图片在 `main/epd_image.h`，每次上电或复位刷新一次，结束后关闭升压。此程序不支持上位机协议；烧录它会替换 USB 接收程序。

根目录 `build_idf54.ps1` 保留原电脑的安装路径，换电脑建议使用上述标准命令。

可选软件 SPI 对照测试（同一组引脚）：

```powershell
idf.py -D EPD_SOFTWARE_SPI=ON build
idf.py -p COM9 flash
```

恢复默认硬件 SPI 时，重新用 `idf.py -D EPD_SOFTWARE_SPI=OFF build` 编译后烧录。

## 图片处理与数据格式

### 原图要求

原图不必预先做成 104 × 212。上位机根据横竖屏方向缩放、裁切，再转换到屏幕原生数据。

- 原生方向：104 × 212；横屏编辑：212 × 104。
- 显示颜色只有黑、白、红；照片灰度可通过黑白抖动近似表现。
- 文字、线稿、图标通常适合清晰阈值；照片可以尝试抖动。
- 铺满裁切时注意边缘，重要内容可能被裁掉。
- 预览表示转换后的像素，实际色彩和对比度以实屏为准。

### 导出格式

| 格式 | 内容与用途 |
| --- | --- |
| PNG | 当前方向的黑白红预览 |
| C 数组 | 原生 104 × 212 点阵，含 `epd_image_black` 与 `epd_image_red` |
| BIN | 原生图像数据，共 5512 字节，不含文件头 |

BIN 编码：黑层 2756 字节在前，红层 2756 字节在后；按原生图像逐行打包，每字节高位在前。每层中 1 表示使用对应颜料，0 表示不使用；黑红都为 0 表示白色，禁止黑红同时为 1。

根目录单图工程也可使用 Windows PowerShell 转换脚本：

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\convert_image.ps1 -InputPath "D:\Pictures\example.jpg" -BlackThreshold 180
```

脚本使用 Windows `System.Drawing`，更新内置点阵和预览；之后需要重新编译、烧录单图固件。

示例转换预览如下，仅展示点阵输出：

![单图示例的黑白红转换预览](assets/epd_preview_4x.png)

## 保存与重绘

| 操作 | 行为 |
| --- | --- |
| 不勾“同时保存到设备” | 传图并刷新，不把此次图片写入设备 Flash |
| 勾选“同时保存到设备” | 刷新流程成功后写入 NVS，覆盖旧图 |
| 重绘已存图片 | 从 NVS 读取通过校验的图片并刷新，无需电脑重新传输 |
| 设备重新上电 | 读取保存状态，等待指令，不自动重绘 |

墨水屏断电后可保留已有画面，这一特性不依赖 NVS 保存。NVS 保存的是以后可重新刷出的图像数据。保存内容 CRC 与现有记录相同时，固件跳过重复写入。

NVS 初始化失败时，接收固件 1.2 报告存储状态，上位机禁用保存功能，仍可取消保存后传图。固件不会自动擦除原 NVS。

## 屏幕驱动流程

驱动顺序参考 Pervasive Displays `PDLS_EXT3_Basic_Global` 的 Small C/J 系列流程。

```text
主机发送完整图像
    ↓
检查尺寸、长度、CRC32 与黑红色层重叠
    ↓
屏幕硬件复位 → 软件复位 → 温度参数与 PSR 初始化
    ↓
0x10 写入黑层 → 0x13 写入红层
    ↓
等待 BUSY 就绪 → 0x04 开启 DC/DC → 等待就绪
    ↓
0x12 全屏刷新 → 等待就绪
    ↓
0x02 关闭 DC/DC → 等待就绪 → 拉低屏幕 RST/DC
    ↓
按需保存 NVS → 回复完成
```

- 软件复位使用 `0x00 / 0x0E`，随后延时 5 ms，再写温度和 PSR。
- 温度参数当前固定为 25℃，未接入实时测温；建议室温下使用和验证。
- 复位后按定时时序继续初始化，第一个 BUSY 等待位于图像传输后、开启升压前。
- BUSY=0 表示忙；程序要求高电平采样持续跨越 200 ms 才判定就绪。
- 每个 BUSY 等待阶段最多 120 秒，超时停止后续流程并保持屏幕复位，不强行跳过等待。
- 接收固件将小于 1 秒就返回就绪的刷新视为异常。正常全刷通常需要数十秒。
- 刷新期间不接受另一张图片，进度表示所处阶段，不是精确剩余时间。
- 串口断开不会中止已开始的刷新，刷新期间应保持供电。

CRC 和 `accepted` 回复证明 C3 收到并校验了图像。屏幕 SPI 没有图像回读确认，`done` 只表示驱动流程完成，最终仍应观察实际画面。

## Wi-Fi 无线传图（接收固件 1.2）

不需要路由器：C3 自建 2.4 GHz 热点，电脑连接它后使用 HTTP 传图。USB 传图继续可用，首次安装或升级固件仍使用 USB；这不是 OTA 无线烧录。

1. 先用 USB 数据线连接 C3，在上位机 **USB** 模式选择实际 COM 口，点击“首次安装接收固件”，烧入本次提供的 1.2 固件。旧 1.1 固件没有 Wi-Fi 功能。
2. 保持 C3 和屏幕 PCB 供电，电脑 Wi-Fi 连接 **InkStudio-C3**，密码 **inkstudio123**。Windows 提示“无 Internet”时选择保持连接；这个热点不提供互联网。
3. 打开新版上位机，在顶部选择 **Wi-Fi**，地址填写 **192.168.4.1**，点击“连接设备”，等待握手成功。
4. 导入和调整图片，点击“发送到墨水屏”。保存、重绘、CRC 检查及刷新阶段提示与 USB 相同。
5. 网络断开后，设备可能仍在刷新，请继续供电。上传响应丢失时，程序不会自动重传；重新连接并确认屏幕及设备忙状态后再决定是否发送。

Wi-Fi 只替代数据传输，不能给屏幕供电。热点默认最多连接两台设备，同一时间全设备仅接受一个刷新任务。Wi-Fi 工作时功耗和供电需求高于 USB 单独待机；供电不稳可能重启。

默认密码供本地测试使用，知道热点密码的人可以操作屏幕。可在接收工程运行 `idf.py menuconfig` → **Ink Studio Wi-Fi** 修改热点名称和密码（8～63 个 ASCII 字符），然后重新编译烧录。不会把家庭 Wi-Fi 凭据写入工程。当前没有自动联网、浏览器图片编辑页面或无线升级；浏览器访问 `http://192.168.4.1/` 仅返回接收器说明。

若搜不到热点，确认烧入的是本次 1.2 接收固件并按 RESET；若连上后无法握手，检查电脑仍连接该热点、IP 地址、VPN/代理或防火墙。上位机 HTTP 客户端直接连接设备，不使用系统 HTTP 代理。固件网络初始化失败时会记录日志，USB 接收仍保留。

## USB 通信协议

协议版本为 1，使用带前缀的单行 JSON：

```text
@EPD1 {JSON对象}\n
```

前缀是 ASCII 字符 `@EPD1 `，末尾是一个真实换行字节。上位机串口参数为 115200，底层使用 C3 原生 USB CDC / Serial-JTAG，不是外置 UART 转串口。

### 请求

| `cmd` | 用途 | 额外字段 |
| --- | --- | --- |
| `hello` | 握手与查询设备状态 | 无 |
| `status` | 查询状态，返回 `hello` 事件 | 无 |
| `image` | 发送一帧并刷新 | `width`、`height`、`crc`、`save`、`data` |
| `restore` | 重绘 NVS 中的一张图片 | 无 |

每个请求的 `id` 是 1～2147483647 的整数，用于对应回复。握手示例：

```text
@EPD1 {"id":1,"cmd":"hello"}
```

图像请求尺寸固定为 104 × 212，`data` 为 5512 字节原生图像的 Base64；`crc` 为这 5512 字节的 CRC32，使用 8 位小写十六进制字符串，算法与 Python `zlib.crc32` 一致。

可在 `image_tool` 中复用编码函数生成消息：

```python
from protocol import encode, image_command

# payload 是黑层 + 红层，共 5512 字节。
message = image_command(request_id=2, payload=payload, save=False)
packet = encode(message)
# 将 packet 写入已打开的串口。
```

### 回复

| `event` | 含义 |
| --- | --- |
| `ready` | 固件启动通知，不代替 `hello` 握手 |
| `hello` | 协议版本、固件标识、尺寸、忙状态、保存及存储状态 |
| `accepted` | 图像通过校验并提交刷新任务，尚未刷新完成 |
| `stage` | 复位、写图、升压、刷新、关闭升压、保存或等待信息 |
| `done` | 流程结束，含 CRC、是否保存及耗时 |
| `error` | 校验、刷新、存储等错误 |

当前固件标识 `EPD_BOARD2_RECEIVER_1.2`。单行接收缓冲上限 10000 字节；未接收完整的请求中断超过 10 秒后被丢弃。上位机握手超时 10 秒，图像任务等待超时 400 秒；任务超时表示结果未知，不自动重复刷新。

### HTTP 接口

网络传输复用上述 `@EPD1 JSON\n` 命令内容和 JSON 事件字段：

- 每次连接生成一个随机 32 位十六进制 `client`，相同连接的请求与进度查询使用同一值。
- 连接时先 `GET /api/events?client=<client>`，保存返回的 `cursor`、`boot`，该请求不回放历史事件。
- `POST /api/command?client=<client>`，请求体为完整命令包，最长 10000 字节。HTTP `{"ok":true}` 仅表示命令已交给解析器，是否接受以事件中的 `accepted` 或 `error` 为准。
- 每约 300 ms `GET /api/events?client=<client>&after=<cursor>`，返回 `events`、新的 `cursor`、`boot`、`lost`。事件只发给所属会话，USB 回复仍仅发往 USB。
- 设备保存最近 32 个 Wi-Fi 事件；`lost=true` 或 `boot` 改变时停止确认任务，提示结果未知，重新连接后查询状态。断线、超时、响应丢失均不会自动重发图片或重绘命令。
- 两种传输共用命令互斥锁、忙状态和唯一显示任务；网络处理线程不执行屏幕刷新。热点使用 WPA2-PSK，HTTP 本身未加 TLS，不用于公网暴露。

## 测试与打包

### 自动测试

在 `image_tool` 目录，安装依赖后运行：

```powershell
.\.venv\Scripts\python.exe -m unittest -v test_core test_gui test_device test_wifi
```

当前 21 项测试覆盖图像位序、横竖屏映射、黑红不重叠、透明背景、导出、协议分包与坏包恢复、模拟握手/传图/保存/重绘，以及串口错误、握手超时、存储错误恢复和真实 HTTP 套接字模拟的 Wi-Fi 传图、保存重绘、CRC 错误、断线不重传、重启及事件丢失处理。

测试使用图像数据和模拟设备，不验证实物焊接、升压电路或屏幕最终成像。

### 打包 Windows 上位机

在 `image_tool` 目录执行：

```powershell
.\.venv\Scripts\python.exe -m pip install PyInstaller==6.22.3
powershell -ExecutionPolicy Bypass -File .\package.ps1
```

输出 `image_tool/dist/InkStudio-WiFi/`，必须分发整个目录。脚本复制预编译接收固件、烧录脚本和说明，并运行 EXE 自检。

打包时暂时限制 PATH，避免其他软件的同名 ICU / OpenSSL DLL 混入，结束后恢复环境。自检使用模拟设备，检查预览、握手、CRC 传图、保存、重绘和断开；报告写入 `image_tool/logs/packaged_self_check.json`。

### 生成交付 ZIP

先完成上位机打包，再在仓库根目录执行，输出位置必须在仓库外：

```powershell
.\image_tool\.venv\Scripts\python.exe .\tools\package_delivery.py --output "D:\epd_delivery"
```

生成“墨水屏完整源码.zip”和“墨水屏上位机使用包.zip”，附交付说明与压缩包 SHA256。包内有逐文件 SHA256 清单；源码包排除本地环境、构建缓存、sdkconfig 本机配置、导出文件和历史备份。使用包默认读取 `image_tool/dist/InkStudio-WiFi/`；若打包 EXE 时指定了 `-Name`，交付命令需使用对应的 `--name`。同名 ZIP 已存在时拒绝覆盖，请选择新输出目录。

上传 GitHub 时提交源码与说明即可，生成的 Windows 使用包适合放在仓库 Releases。根目录 `.gitignore` 不会影响已被 Git 跟踪的文件。

## 常见问题

### 插了 USB，为什么连接不上？

确认连接 C3 自身 USB 口，线缆支持数据，设备管理器有对应 COM 端口，再检查是否已安装接收固件。原单图显示程序不会回应上位机协议。

### `PermissionError(13)` / 拒绝访问 / 端口占用

同一 COM 端口只能由一个程序打开。退出 IDF monitor（`Ctrl+]`）、Arduino 串口监视器、其他上位机实例后重试。

### USB 写入超时或握手超时

端口能打开不表示固件支持协议。先安装配套接收固件；已安装时按 C3 RESET 或重新插拔 USB，再扫描连接。握手失败后上位机会释放端口，便于重新烧录。

### 安装按钮找不到 Python / esptool

按首次安装一节安装工具，或在已有 ESP-IDF 环境中用命令行烧录。普通图片编辑、预览和模拟设备不需要烧录工具。

### 日志卡在 `before power on` / `power on` / `refresh`

分别表示开启升压前、升压后、刷新后的 BUSY 等待。不要强行跳过；等错误信息明确阶段后，断电检查引脚、排线、供电和外围升压电路。BUSY 读数本身不能证明升压电压正常。

### 显示完成但屏幕没变化

确认选的是真实端口而非模拟设备，再检查引脚与供电。CRC 验证电脑到 C3 的数据，SPI 指令发出不表示屏幕一定正确接收并成像。

### 能显示，但无法保存

查看握手返回的 `storage_error`。存储不可用时可取消保存后传图。当前固件保留 NVS 原数据，不自动擦除恢复。

### 不保存，断电后画面还在吗？

可以。墨水屏形成的画面可断电保留，保存到 NVS 则用于日后重新绘制，两者作用不同。

### 能局刷或每秒更新吗？

当前仅实现全刷，不能每秒更新，没有局刷接口。局刷需要匹配具体屏幕型号、控制器和厂家支持的驱动流程，仅发送局部图像数据并不能实现。

### 支持其他屏幕、Linux 或 macOS 吗？

当前只针对上述屏幕与新 PCB 验证。其他屏幕需核对分辨率、点阵编码、引脚、初始化和波形；Linux / macOS 的源码运行、串口与烧录流程尚未验证，Windows PowerShell 脚本不能直接照搬。

## 后续开发方向

以下是可继续实现的方向，当前尚未提供：

- 补充真实 USB/Wi-Fi 传图、保存及断电恢复的可复现硬件回归记录。
- 屏幕驱动、图像传输与应用任务进一步模块化。
- 实时温度参数、受控刷新间隔与更多屏幕配置。
- 多图存储、图库选择与定时轮播。
- 屏幕低功耗策略，以及匹配 USB 场景的 ESP32 休眠 / 唤醒。
- 厂家确认具体屏幕支持后，评估局刷驱动。

## 参考资料与许可证

- [ESP-IDF v5.4 ESP32-C3 文档](https://docs.espressif.com/projects/esp-idf/en/v5.4/esp32c3/)
- [Pervasive Displays PDLS_EXT3_Basic_Global](https://github.com/PervasiveDisplays/PDLS_EXT3_Basic_Global)：初始化与全刷时序参考。
- [上位机详细使用说明](image_tool/使用说明.md)
- [交付说明](交付说明.md)
- [版本记录](CHANGELOG.md)

仓库当前尚未包含项目级 `LICENSE` 文件，本 README 不授予额外许可。第三方组件及引用资料遵循各自许可证；项目许可证可在维护者确定后单独补充。
