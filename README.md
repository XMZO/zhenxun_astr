# zhenxun_astr

将真寻的基础签到流程迁移到 AstrBot，并使用真寻资源仓库中的默认签到卡片布局。

## 安装

在 AstrBot WebUI 打开 `插件管理`，点击右下角的 `+`，选择通过仓库地址安装并填写：

```text
https://github.com/XMZO/zhenxun_astr
```

安装完成后启用或重载“真寻签到”即可。仓库默认 `main` 分支只包含插件运行文件和预装模板，不包含可视化编辑器、测试、研究仓库或本地生成结果。

## 当前功能

- `签到`、`打卡`：每天按配置时区记录一次签到并发送签到图片。
- `我的签到`、`签到状态`：查看自己的签到信息并发送原版风格图片。
- 同一天重复签到不会重复增加次数或奖励；重复查看使用“我的信息”卡片区。
- 图片渲染失败时回退为文字回复。
- 自动发现并切换多个签到模板包；一个文件夹或 ZIP 就是一套模板。
- 金币和道具字段已经预留，默认关闭，不连接商店，也不执行道具效果。
- 不注册好感度排行、签到排行或其他排行指令。

## 原版模板来源

官方真寻资源仓库：

`https://github.com/zhenxun-org/zhenxun-bot-resources.git`

本插件当前使用的资源提交为 `08ae663cb7fabfc64c11ddad2489b55fa86cf96f`，资源版本文件为 `1.1.1`。

- `sign_card.css` 与官方 `themes/default/pages/builtin/sign/style.css` 保持一致。
- `sign_card.html` 保留官方 `wrapper`、定位尺寸、字体族、图片层级和布局，只将真寻渲染器的 `extends/include/asset` 改为 AstrBot 可传入的数据变量。
- `assets/sign/img` 是官方签到目录的原始图片，包含人物、日历牌、心形、标签和天气图标。
- `assets/sign/fonts` 包含官方签到使用的五种字体。
- `sign_card.manifest.json` 保留官方卡片尺寸元数据。

官方源码中的卡片由 `main.html`、`style.css`、`manifest.json` 和主题素材共同生成，不是 Python 直接绘制的图片。

## 为什么要内嵌素材

AstrBot 的远程 T2I 服务不能读取插件机器上的 `file://` 路径，也不能稳定加载模板中的外部资源 URL。插件会把本地官方字体和图片转为 `data:` URI 后再提交渲染，因此最终布局和字体不依赖渲染服务是否能访问本地目录。

原版卡片宽高为 `465×926`。AstrBot 远程端点默认生成较宽的截图，插件会在收到图片后裁切左上角的原版区域，并输出真正的 PNG。

内嵌五种字体会让单次渲染请求变大，远程渲染通常比普通文字图片慢一些，这是保持原版字体的代价。

## 多模板包

推荐直接在 AstrBot WebUI 中安装模板：

1. 打开 `插件管理 -> 真寻签到 -> 插件配置`。
2. 在“签到模板管理”中点击上传，选择编辑器生成的一个或多个 ZIP。
3. 关闭上传弹窗后点击配置页底部的“保存”。AstrBot 会热重载插件，插件会校验并安装模板。
4. 默认会自动应用最后一个新增或更新成功的模板；发送 `签到模板` 可确认当前模板和最近导入结果。

上传控件只接受 ZIP。上传源保存在 AstrBot 管理的插件文件区，安装副本保存在插件数据目录的 `template_packs`；两者相互独立。在上传弹窗中删除源文件不会卸载已经安装的模板。关闭“自动应用新上传的模板”后，可以继续使用 `切换签到模板` 手动选择。

也可以直接部署文件。模板会从以下两个目录自动发现，无需重载插件：

- 插件目录的 `template_packs`。
- AstrBot 数据目录的 `plugin_data/astrbot_plugin_zhenxun_sign/template_packs`。

每个子文件夹或 `.zip` 是一套独立模板。包内的 `template.json` 保存名称、卡片尺寸和该模板自己的文字设置；`sign_card.html`、`sign_card.css` 与 `assets/sign` 保存布局和素材。ZIP 可以直接包含这些文件，也可以在外面多包一层同名目录。

发送 `签到模板` 查看当前模板与自动发现结果。列表只显示模板名称，不显示内部 ID。管理员可以按名称或列表序号切换：

```text
切换签到模板 <模板名称>
切换签到模板 #<序号>
```

生成器会为每套新模板创建随机内部 ID，它只用于防重复和保存选择状态，与对外显示名称完全分离。同名模板可以共存，此时使用 `#序号` 切换。选择结果保存在插件数据目录的 `active_template.txt`，不会写入或合并 AstrBot 插件配置。

内置官方模板不会被自定义包覆盖。无效或损坏的包会被忽略；当前自定义模板渲染失败时，插件会尝试用内置模板完成本次图片回复。

## 模板内容

卡片的品牌名称、日期格式、头像、标题、问候语、奖励/信息文字、早晚文案池和底部预留区都由当前模板决定，不占用 AstrBot 插件配置。默认模板保存在根目录的 `template_settings.json`，自定义模板保存在各自 `template.json` 的 `settings` 中，因此切换模板会同时切换文字与素材。

推荐使用可视化编辑器修改这些内容并生成新模板包。AstrBot 设置页只保留模板安装、签到时区、UID 隐私和奖励预留等运行参数。昵称、签到次数、日期、金币等实时数据仍由插件在渲染时传入模板。

## 本地预览与修改

可视化编辑器保存在同一仓库的 [`editor-v0.3.0` 标签](https://github.com/XMZO/zhenxun_astr/tree/editor-v0.3.0)，不会随 AstrBot 的默认分支安装。它支持实时预览、素材替换、图层选框、拖动、八向拉伸、撤销/重做和恢复原版。

单独获取编辑器：

```powershell
git clone --branch editor-v0.3.0 --single-branch https://github.com/XMZO/zhenxun_astr.git
cd zhenxun_astr
cd template_editor
uv run editor_server.py
```

打开 `http://127.0.0.1:8780/`。点击“生成模板”会同时：

- 创建 `template_editor/output` 下的独立目录与 ZIP。
- 自动安装到 `template_packs/<随机内部ID>`，内部 ID 不在机器人界面展示。
- 在结果弹窗中给出可直接发送的切换命令。

把 ZIP 搬到其他 AstrBot 实例时，直接通过插件设置页的“签到模板管理”上传并保存即可。

生成过程不会覆盖内置模板或已有自定义模板。每个包内都会生成独立 `README.md` 安装教程，详细操作也可见 `template_editor/README.md`。

## 基础预览服务器

直接双击 `sign_card.html` 看不到最终效果，因为它是带 Jinja 变量的模板，图片和字体也会在运行时注入。`editor-v0.3.0` 标签提供了一个不依赖 AstrBot 的本地预览服务器；先按上一节获取该版本，再在仓库根目录运行：

```powershell
uv run --with jinja2 preview.py
```

然后打开 `http://127.0.0.1:8765/`。预览服务器每次请求都会重新读取 `sign_card.html`、`sign_card.css` 和 `assets/sign`，所以修改后刷新浏览器即可查看；示例文字和默认数据可以编辑 `preview_data.json`。实际插件仍需在 AstrBot 中重载后才会采用修改。

可用查询参数快速切换示例状态：

- `?mode=view`：查看“我的信息”区域。
- `?weather=0` 到 `?weather=11`：切换天气图标。
- `?tag=0` 到 `?tag=5`：切换标签图标。
- `?temperature=26`：指定预览温度。
- `?name=测试用户`：指定预览昵称。

例如：`http://127.0.0.1:8765/?mode=view&weather=4&tag=2&temperature=26`

预览服务器只监听本机回环地址，不会对外提供服务。

## 好感度兼容预留

当前插件不计算、不保存、不排行好感度。为了保持原版卡片的高度和底部布局，原好感度区域显示静态的“未接入”占位值，数据只来自当前模板的 `reserved_panel`。

未来若接入 `astrbot_plugin_Favour_Ultra`，可以把它作为独立扩展模块向卡片数据提供这些字段；本版本不包含该兼容模块，也不写死对另一个插件的依赖。

## 金币与道具预留

`rewards.enable_gold` 和 `rewards.enable_item_placeholder` 默认都是 `false`。开启后目前只写入本插件自己的 JSON 字段并显示在卡片上：

- 不连接 AstrBot 商店。
- 不迁移或读取真寻商店数据。
- 不实现道具购买、使用和效果。

## 数据与日期

运行数据默认保存在：

`data/plugin_data/astrbot_plugin_zhenxun_sign/sign_data.json`

日期保存为 ISO 日期字符串，例如 `2026-08-10`。每日判重只比较配置时区下的当前日期，没有预生成日期表，也没有 2025 或 2026 的截止值，因此未来年份和跨年都可以正常工作。

写入使用同目录临时文件加原子替换。JSON 损坏时，原文件会先改名为带时间戳的 `.corrupt-...json`，再创建空数据。

## 手动安装

无法通过仓库地址安装时，可以下载默认 `main` 分支 ZIP，解压到 AstrBot 的 `data/plugins/zhenxun_astr`，随后重载插件或重启 AstrBot。运行环境需要能使用 AstrBot 的 HTML/T2I 渲染端点。

## 暂缓内容

- 好感度计算、排行和与 `astrbot_plugin_Favour_Ultra` 的模块化兼容。
- 真寻旧数据库自动迁移。
- 金币商店连接。
- 道具购买、使用和实际效果。
