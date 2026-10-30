const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');

// Runs the helper a game includes (game_i18n_helper.js) in a fake page that has the strings block.
function page(table){
  const document={
    documentElement:{lang:'',dir:''},
    getElementById:id=>id==='i18n-strings'?{textContent:JSON.stringify(table)}:null,
  };
  const context=vm.createContext({document,JSON});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../../game_i18n_helper.js'),'utf8'),context);
  return {document,t:(key,params)=>vm.runInContext(`t(${JSON.stringify(key)},${JSON.stringify(params)})`,context),
    apply:message=>vm.runInContext(`applyLocale(${JSON.stringify(message)})`,context)};
}

const TABLE={your_turn:'Your turn',wins:'{name} wins!',moves:'Moves: {count}'};

test('without a translation the game shows its own texts, with the placeholders filled in',()=>{
  const game=page(TABLE);
  assert.equal(game.t('your_turn'),'Your turn');
  assert.equal(game.t('wins',{name:'Ana'}),'Ana wins!');
  assert.equal(game.t('moves',{count:3}),'Moves: 3');
  assert.equal(game.t('wins',{}),'{name} wins!'); // a missing value stays visible, not "undefined"
  assert.equal(game.t('unknown_key'),'unknown_key');
});

test('the platform\'s translation replaces the texts, and what it lacks falls back to English',()=>{
  const game=page(TABLE);
  game.apply({locale:'es',direction:'ltr',strings:{wins:'¡{name} gana!'}});
  assert.equal(game.t('wins',{name:'Ana'}),'¡Ana gana!');
  assert.equal(game.t('your_turn'),'Your turn');
  assert.equal(game.document.documentElement.lang,'es');
});

test('a right-to-left language flips the page',()=>{
  const game=page(TABLE);
  game.apply({locale:'ar',direction:'rtl',strings:{your_turn:'دورك'}});
  assert.equal(game.document.documentElement.dir,'rtl');
  assert.equal(game.t('your_turn'),'دورك');
});

test('a message without translations changes nothing',()=>{
  const game=page(TABLE);
  game.apply({type:'state_changed'});
  assert.equal(game.t('wins',{name:'Bo'}),'Bo wins!');
  assert.equal(game.document.documentElement.lang,'');
});

test('going back to English restores the original texts',()=>{
  const game=page(TABLE);
  game.apply({locale:'es',strings:{your_turn:'Tu turno'}});
  game.apply({locale:'en'});
  game.apply({strings:{}}); // an English message carries no strings
  assert.equal(game.t('your_turn'),'Your turn');
});
