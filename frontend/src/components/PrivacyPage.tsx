/**
 * 公开隐私说明。文案里的期限必须与实现一致：
 * 验证码默认 300 秒、允许 60–1800 秒（core/config.py）；
 * 发码记录在下次发送时清理 48 小时前的行（repository/sms_auth.py SMS_RECORD_CLEANUP_HOURS）；
 * 安全日志 184 天（api/audit.py RETENTION_DAYS）；
 * 未发送附件默认 24 小时；定时任务检查点和操作回执默认 30 天。
 * 没有实现自动清理的资料不要写具体天数。
 */
import { Button } from "antd";
import { useEffect, useState } from "react";
import { Icon } from "./Icon";
import { getAuthConfig } from "../api/auth";
import { publicOperatorEmail } from "../lib/privacyMail";
import "../styles/privacy.css";

type ContactState = "loading" | "ready" | "unknown";

export function PrivacyPage() {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [contact, setContact] = useState<ContactState>("loading");
  useEffect(() => {
    let active = true;
    void getAuthConfig().then((config) => {
      if (!active) return;
      setName((config.operator_name ?? "").trim());
      setEmail(publicOperatorEmail(config.operator_email));
      setContact("ready");
    }).catch(() => { if (active) setContact("unknown"); });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    if (window.location.hash !== "#requests") return;
    const section = document.getElementById("requests");
    section?.scrollIntoView();
    section?.focus();
  }, []);
  return (
    <main className="privacy-page">
      <article className="privacy-sheet privacy-document">
        <div className="privacy-document-heading">
        <Button
          className="action-secondary privacy-document-back"
          href="/"
          icon={<Icon name="arrow-up" size={16} rotate={-90} />}
          onClick={(event) => {
            if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
            if (window.history.length > 1) {
              event.preventDefault();
              window.history.back();
            }
          }}
        >
          返回
        </Button>
        <h1>MelonClaw隐私政策</h1>
        </div>
        <p>更新日期：2026 年 11 月 28 日。本次更新自发布之日起生效。</p>
        <p>{name ? `本服务由${name}运营（以下称“我们”）。` : "本服务由个人运营（以下称“我们”）。"}本政策适用于本服务的注册、登录及各项功能，说明我们如何处理和保护您的个人信息，以及您管理个人信息的方式。请您在使用服务前阅读本政策。</p>

        <h2>一、我们如何收集和使用信息</h2>
        <h3>1. 账号注册与登录</h3>
        <p>我们处理您的用户 ID、账号名称及密码哈希，用于账号管理和身份验证。当您使用手机号注册、短信登录或绑定手机号时，我们将完整手机号交由阿里云号码认证服务发送和核验验证码。平台保存手机号的带密钥摘要和后四位，用于识别已绑定的号码及显示尾号，不保存完整手机号或短信验证码。</p>
        <p>为限制短信发送频率及记录核验状态，我们保存号码摘要、用途、发送次数、核验状态、尝试次数、有效期和相关验证编号。自助注册成功后，我们留存用户 ID、注册及核验时间、核验方式、验证编号和手机号尾号，用于记录注册时的手机号验证、处理账号问题和保障服务安全。短信验证确认您能使用该手机号，不核验您的姓名或身份证件。</p>
        <h3>2. 智能对话、文件与知识库</h3>
        <p>当您使用智能对话、文件和知识库功能时，我们处理您提交的消息、文件及相关内容，以提供服务、保存对话历史和支持检索。定时任务及长期记忆功能会保存您配置或使用这些功能时产生的相关信息。</p>
        <p>为生成回复，系统会向您所用模型的服务提供方发送完成请求所需的内容。部分附件需要识别时，文件会发送至 DeepSeek OCR 服务。知识库导入和检索会将相关文本发送至部署配置的嵌入服务。使用语音输入时，录音会发送至 DeepSeek 语音识别服务进行转写，平台不持久保存音频。您启用外部工具或 MCP 连接后，执行操作所需的信息可能发送至相应服务。第三方服务的处理方式以其服务条款及隐私政策为准。</p>
        <h3>3. 安全运行与投诉举报</h3>
        <p>为保障账号和服务安全、排查异常及追溯安全事件，我们记录注册、登录及其他写操作相关的账号、时间、操作类型、请求结果、网络地址和端口，以及浏览器提供的客户端信息，包括 User-Agent、平台信息和随机设备标识。安全审计不记录密码、验证码或聊天正文。</p>
        <p>您可以通过登录页或登录后头像菜单的「意见反馈」提交问题、投诉或举报。我们处理您主动填写的原因、说明和联系方式，用于受理、核查、反馈和记录处理结果。相关信息由有权限的管理人员按职责处理。</p>

        <h2>二、浏览器存储与第三方服务</h2>
        <p>浏览器使用本地存储保存登录令牌，在当前标签页临时保存登录表单草稿，并使用设备标识辅助安全审计。退出登录或完成相应操作时，部分本地信息会被清除。您也可以通过浏览器设置清除本地数据，清除后可能需要重新登录。</p>
        <p>短信核验、模型、文档识别和嵌入等功能依赖相应的外部服务，具体服务取决于功能和部署配置。如果开启调用监控并允许采集内容，脱敏后的对话文本会发送至所配置的监控服务。</p>
        <p>使用手机号注册、短信登录或绑号时，阿里云号码认证服务接收手机号以发送和核验验证码。使用需要识别的文件或语音输入时，DeepSeek 接收相应文件或录音以提供识别服务。有关服务提供方的信息，可参阅<a href="https://terms.aliyun.com/legal-agreement/terms/suit_bu1_ali_cloud/suit_bu1_ali_cloud202107091605_49213_4_9_18441.html" target="_blank" rel="noreferrer">阿里云隐私政策</a>和<a href="https://cdn.deepseek.com/policies/zh-CN/deepseek-open-platform-privacy-policy.html" target="_blank" rel="noreferrer">DeepSeek 开放平台隐私政策</a>。模型、嵌入、监控和外部工具的其他服务提供方及其数据处理方式取决于实际配置和您的选择，您可以联系运营方了解。</p>
        <p>服务端数据保存在部署所使用的数据库、数据目录和工作区；第三方接收的信息由相应服务处理，其存储地点和处理方式可能不同。服务提供方及其所在地区可能随部署配置或您的选择而变化；有关具体部署、存储地点和可能涉及的境外传输，您可以联系运营方了解。</p>

        <h2>三、我们如何保护和保存信息</h2>
        <p>我们通过账号认证、权限校验、密码哈希、手机号摘要、访问控制及安全审计等措施保护信息。用户数据由服务端按账号和所属项目等权限进行访问控制。</p>
        <p>账号资料、手机号摘要和尾号、注册核验记录、对话、已保存文件、知识库及长期记忆目前没有统一自动删除期限。您可在产品内删除自己的会话。未发送的聊天附件默认在 24 小时后清理，定时任务检查点和操作回执默认保留 30 天。安全审计日志至少保留 184 天，并在后续写入时清理过期文件。</p>
        <p>短信验证码默认有效期为 5 分钟，部署配置可调整为 1 分钟至 30 分钟。短信发码和次数记录在后续发码请求时清理创建或更新早于 48 小时的记录，未发生新的发码请求时不会按固定日程清理。数据库、数据目录和工作区的备份可能仍包含已在线上处理的信息，备份及第三方服务的保存期限取决于相应安排。</p>

        <section id="requests" className="privacy-document-section" tabIndex={-1} aria-labelledby="privacy-request-title">
          <h2 id="privacy-request-title">四、如何管理您的个人信息</h2>
          <p>您可以通过产品功能管理账号和删除自己的会话，也可以联系运营方申请查阅、更正信息、注销账号或删除指定信息。为保护账号安全，我们会在处理前核验您的身份。</p>
          <p><strong>注销账号后，账号将无法登录，手机号绑定会释放。现有聊天、文件和注册核验记录不会因注销自动删除。</strong>如需删除指定信息，您可以通过下方渠道提出申请；我们会核验账号归属，并告知实际处理结果。已存在的安全日志和备份不会因申请自动消失；依法需要保留的信息及备份的处理，以适用的留存要求和实际备份安排为准。</p>
          <p>我们会在收到申请后核验账号归属，按申请内容处理并回复。核验时请不要提供密码或短信验证码。您也可以通过「意见反馈」查询投诉的处理进度。</p>
          <h2>五、如何联系我们</h2>
          <p>您可以通过登录页的「意见反馈」提交问题或查询处理进度。个人信息申请请使用以下联系渠道。</p>
          {email ? <p>联系邮箱：<a href={`mailto:${email}`}>{email}</a></p>
            : contact === "ready" ? <p>暂未提供个人信息申请邮箱，请通过登录页的「意见反馈」联系运营方。</p>
              : contact === "loading" ? <p>正在获取联系方式。</p>
                : <p>联系方式暂时无法获取，请稍后重试。</p>}
        </section>
      </article>
    </main>
  );
}
