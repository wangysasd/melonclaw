import { Button } from "antd";
import { Icon } from "./Icon";
import "../styles/privacy.css";

export function TermsPage() {
  return <main className="privacy-page">
    <article className="privacy-sheet privacy-document">
      <div className="privacy-document-heading">
        <Button className="action-secondary privacy-document-back" href="/" icon={<Icon name="arrow-up" size={16} rotate={-90} />} onClick={(event) => {
          if (!event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey && window.history.length > 1) {
            event.preventDefault();
            window.history.back();
          }
        }}>返回</Button>
        <h1>用户协议</h1>
      </div>
        <p>欢迎使用本服务。注册或登录前，请阅读本协议及<a href="/privacy">隐私政策</a>。本服务的运营主体及联系方式可在隐私政策中查看。</p>
        <h2>服务内容</h2>
        <p>本服务提供智能对话、文件处理、知识库、定时任务及可配置的工具连接。具体功能以当前部署和账号权限为准。部分功能由第三方模型或服务提供，使用时相关内容会按功能需要发送至对应服务。</p>
        <h2>账号使用</h2>
        <p>请提供真实、有效的注册信息，妥善保管账号、密码和验证码，发现账号异常时及时联系运营方。您可以通过隐私政策所列渠道申请注销账号。注销后的信息处理以隐私政策中的说明为准。</p>
        <h2>使用规范</h2>
        <p>您应遵守法律法规，尊重他人合法权益，不得利用本服务制作、发布或传播违法有害信息，不得侵犯他人的知识产权、隐私和个人信息，不得未经授权访问、干扰或破坏系统。上传或提交他人信息时，请确保具有合法依据。</p>
        <p>请合理配置和使用模型、工具及外部服务，并确认您有权处理所提交的数据和执行相关操作。涉及文件修改、外部连接和后台任务的操作，请结合提示审慎使用。</p>
        <h2>生成内容</h2>
        <p>人工智能生成的内容可能不准确、不完整或不适合特定用途。请在使用、传播或依据生成内容作决定前自行核实，并依法承担使用内容的责任。</p>
        <h2>投诉与处理</h2>
        <p>您可通过登录页或登录后头像菜单的「意见反馈」提交问题、投诉或举报。运营方将根据实际情况核查处理，并通过相应渠道反馈。对违反法律法规或本协议的行为，运营方可根据情况采取限制使用等措施。</p>
        <h2>服务变更与联系我们</h2>
        <p>服务功能可能因维护、技术或运营安排发生变化。有关本协议的问题，请通过隐私政策中公开的联系方式联系运营方。</p>
    </article>
  </main>;
}
