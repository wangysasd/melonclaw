import "../styles/compliance.css";

export function ComplianceStatement() {
  return (
    <footer className="compliance-statement" aria-labelledby="compliance-statement-title">
      <h2 id="compliance-statement-title">【本站合规声明】</h2>
      <p>本网站为纯个人学习交流站点，仅向站点所有者及预先约定的少数特定好友开放，不面向任何社会公众提供公开互联网服务，所有内容均为非公开的内部学习研究资料，不对外引流、不做公开传播。</p>
      <p>本站点内所有金融分析内容、数据推演结论、市场相关观点，仅为个人学习过程中的模拟研究记录，不构成任何投资建议、操作指导或交易邀约，所有内容均不代表任何持牌金融机构立场。访问者基于本站内容做出的任何投资决策，产生的全部盈亏由其自行承担，站点所有者不承担任何民事赔偿责任。本站所有内容未承诺保本保收益、未夸大过往收益、未隐瞒任何投资风险，所有金融相关信息仅供访问者个人参考，访问者需自行通过官方持牌金融渠道核实全部关键信息。</p>
      <p>本站内所有原创分析内容、对接外部接口生成的衍生内容，均为个人非商业用途的学习资料，仅授权指定访问者个人内部查阅，禁止任何形式的转载、复制、二次传播或用于商业用途。未经站点所有者书面许可，任何人不得将本站内容对外扩散、发布至公开互联网平台。</p>
      <p>本站已完成ICP备案，所有内容严格遵守国家互联网信息服务、金融信息服务相关法律法规，不从事任何经营性互联网活动，不传播任何违规金融信息，接受监管部门与访问者的监督。</p>
    </footer>
  );
}

export function FilingFooter() {
  return (
    <footer className="filing-footer" aria-label="网站备案信息">
      <a href="https://beian.miit.gov.cn/" target="_blank" rel="noopener noreferrer">京ICP备2026082777号-1</a>
    </footer>
  );
}
